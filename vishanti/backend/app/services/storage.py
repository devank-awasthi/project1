"""Attachment pipeline.

1. presign  - the browser gets a short-lived URL and uploads straight to
              object storage under quarantine/. Large bodies never pass
              through the API process.
2. finalize - the server reads the quarantined object, checks its real type
              from magic bytes (not the client's claim), re-encodes it to
              strip metadata (EXIF GPS, camera serials, PDF author fields),
              computes SHA-256 of the clean bytes, stores it under clean/ and
              deletes the original.
3. the ticket's GENESIS ledger entry records that SHA-256.

STORAGE_BACKEND=local swaps object storage for a folder so the project runs
without MinIO; the API then plays the role of the upload URL.
"""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from app.canonical import sha256_hex
from app.config import settings
from app.redis_client import get_redis
from app.services.tickets import TicketError, UploadedFile

UPLOAD_TTL = 3600

ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def strip_metadata(data: bytes, mime: str) -> bytes:
    if mime.startswith("image/"):
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img.load()
            # Rebuilding from raw pixels drops EXIF, XMP, ICC and text chunks.
            mode = img.mode if img.mode in ("RGB", "RGBA", "L") else "RGB"
            clean = Image.new(mode, img.size)
            clean.putdata(list(img.convert(mode).getdata()))
            out = io.BytesIO()
            fmt = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime]
            if fmt == "JPEG" and mode == "RGBA":
                clean = clean.convert("RGB")
            clean.save(out, format=fmt, quality=90) if fmt != "PNG" else clean.save(out, format=fmt)
            return out.getvalue()
    if mime == "application/pdf":
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(data))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({"/Producer": "vishanti"})  # replaces author/creator/dates
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    raise TicketError(415, "unsupported file type")


# ------------------------------------------------------------------ backends


class _Local:
    def __init__(self) -> None:
        self.root = Path(settings.LOCAL_STORAGE_DIR)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise TicketError(400, "bad key")
        return p

    def put_url(self, upload_id: str, key: str, content_type: str) -> str:
        return f"{settings.PUBLIC_API_URL}/api/uploads/{upload_id}/blob"

    def get_url(self, key: str, filename: str) -> str | None:
        return None  # streamed through the API

    def read(self, key: str) -> bytes:
        p = self._p(key)
        if not p.exists():
            raise TicketError(404, "file not uploaded yet")
        return p.read_bytes()

    def write(self, key: str, data: bytes, content_type: str) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)


class _S3:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        common = dict(
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self.client = boto3.client("s3", endpoint_url=settings.S3_ENDPOINT_URL, **common)
        # Pre-signed URLs must use the host the *browser* can reach.
        self.public = boto3.client(
            "s3", endpoint_url=settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL, **common
        )
        self.bucket = settings.S3_BUCKET

    def put_url(self, upload_id: str, key: str, content_type: str) -> str:
        return self.public.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type}, ExpiresIn=600
        )

    def get_url(self, key: str, filename: str) -> str | None:
        return self.public.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=300,
        )

    def read(self, key: str) -> bytes:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            raise TicketError(404, "file not uploaded yet") from None
        return obj["Body"].read(settings.MAX_UPLOAD_BYTES + 1)

    def write(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = _S3() if settings.STORAGE_BACKEND == "s3" else _Local()
    return _backend


# ------------------------------------------------------------------ flow


def _rkey(upload_id: str) -> str:
    return f"upload:{upload_id}"


async def presign(filename: str, content_type: str, size: int) -> dict:
    if content_type not in ALLOWED:
        raise TicketError(415, "only JPEG, PNG, WebP and PDF files are accepted")
    if size <= 0 or size > settings.MAX_UPLOAD_BYTES:
        raise TicketError(413, f"files must be under {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    upload_id = uuid.uuid4().hex  # 122 random bits: the id is the capability
    key = f"quarantine/{upload_id}"
    meta = {
        "key": key,
        "filename": Path(filename).name[:150],
        "content_type": content_type,
        "size": size,
        "state": "pending",
    }
    await get_redis().set(_rkey(upload_id), json.dumps(meta), ex=UPLOAD_TTL)
    url = await run_in_threadpool(backend().put_url, upload_id, key, content_type)
    return {"upload_id": upload_id, "put_url": url, "headers": {"Content-Type": content_type}}


async def receive_local_blob(upload_id: str, data: bytes) -> None:
    meta = await _meta(upload_id)
    if meta["state"] != "pending":
        raise TicketError(409, "already finalized")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise TicketError(413, "file too large")
    await run_in_threadpool(backend().write, meta["key"], data, meta["content_type"])


async def _meta(upload_id: str) -> dict:
    raw = await get_redis().get(_rkey(upload_id))
    if not raw:
        raise TicketError(404, "upload not found or expired")
    return json.loads(raw)


async def finalize(upload_id: str) -> dict:
    meta = await _meta(upload_id)
    if meta["state"] == "final":
        return {k: meta[k] for k in ("upload_id", "filename", "content_type", "size", "sha256")}
    data = await run_in_threadpool(backend().read, meta["key"])
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise TicketError(413, "file too large")
    real = sniff(data)
    if real is None or real != meta["content_type"]:
        await run_in_threadpool(backend().delete, meta["key"])
        raise TicketError(415, "file content does not match its declared type")
    clean = await run_in_threadpool(strip_metadata, data, real)
    digest = sha256_hex(clean)
    clean_key = f"clean/{upload_id}{ALLOWED[real]}"
    await run_in_threadpool(backend().write, clean_key, clean, real)
    await run_in_threadpool(backend().delete, meta["key"])
    meta.update(state="final", key=clean_key, size=len(clean), sha256=digest, upload_id=upload_id)
    await get_redis().set(_rkey(upload_id), json.dumps(meta), ex=UPLOAD_TTL)
    return {k: meta[k] for k in ("upload_id", "filename", "content_type", "size", "sha256")}


async def take_finalized(upload_ids: list[str]) -> list[UploadedFile]:
    if len(upload_ids) > 5:
        raise TicketError(422, "at most 5 attachments")
    files = []
    for uid in dict.fromkeys(upload_ids):
        meta = await _meta(uid)
        if meta["state"] != "final":
            raise TicketError(409, f"upload {uid} is not finalized")
        files.append(
            UploadedFile(
                upload_id=uid,
                object_key=meta["key"],
                filename=meta["filename"],
                content_type=meta["content_type"],
                size_bytes=meta["size"],
                sha256=meta["sha256"],
            )
        )
    return files


async def forget(upload_ids: list[str]) -> None:
    if upload_ids:
        await get_redis().delete(*[_rkey(u) for u in upload_ids])
