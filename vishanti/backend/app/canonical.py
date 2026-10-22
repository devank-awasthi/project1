"""Deterministic serialisation and hashing used by the audit ledger.

Every hash in the ledger is SHA-256 over *canonical JSON*: keys sorted,
no whitespace, UTF-8. Two machines (or a Python verifier and a JavaScript
one) that hash the same logical record always get the same bytes.

Floats are rejected on purpose: different languages print floats
differently (1.0 vs 1), which would make an honest record look tampered.
Timestamps are stored and hashed as fixed-format strings for the same reason.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

GENESIS_PREV_HASH = "0" * 64
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _check(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        raise TypeError(f"floats are not allowed in hashed payloads ({path})")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"non-string key at {path}")
            _check(v, f"{path}.{k}")
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check(v, f"{path}[{i}]")
        return
    raise TypeError(f"unsupported type {type(value).__name__} at {path}")


def canonical_json(value: Any) -> str:
    _check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_payload(payload: dict) -> str:
    return sha256_hex(canonical_json(payload))


def format_ts(dt: datetime) -> str:
    """UTC, microsecond precision, always the same width."""
    return dt.astimezone(UTC).strftime(TS_FORMAT)


def utcnow() -> datetime:
    return datetime.now(UTC)
