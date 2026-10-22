"""Local embeddings, sentiment, and duplicate clustering.

Primary path: sentence-transformers `all-MiniLM-L6-v2` (384-d) and a small
Hugging Face sentiment model, both running on our own server, so complaint
text (including anonymous complaints) never goes to a third-party API.

Fallback path (EMBEDDINGS_BACKEND=hashing, or "auto" when the model cannot be
loaded): signed feature hashing of words and word pairs into 384 dimensions,
plus a tiny sentiment lexicon. It keeps the pipeline and tests runnable on a
machine without the model, but it only matches shared words: it will NOT see
"Wi-Fi down" and "internet not working" as similar. The real model does.
`backend_name()` reports which one is active and the admin panel shows it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from functools import lru_cache

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EMBEDDING_DIM, ComplaintCluster, Ticket
from app.services.notify import ADMIN_CHANNEL, notify, queue_channel

log = logging.getLogger("vishanti.embeddings")

_STOP = set(
    "a an the is are was were be been to of in on at for and or not no it its this that my our we i me "
    "with from by as has have had do does did since very please sir madam".split()
)
_NEG = set("not broken worst bad dirty slow never unsafe harass harassment fail failed failing down rude angry".split())
_POS = set("good thanks thank resolved fixed better great clean helpful".split())


class _Hashing:
    name = "hashing-fallback"

    def encode(self, text: str) -> list[float]:
        words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        v = np.zeros(EMBEDDING_DIM, dtype=np.float64)
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            v[h % EMBEDDING_DIM] += 1.0 if (h >> 63) & 1 else -1.0
        n = np.linalg.norm(v)
        return (v / n if n else v).tolist()

    def sentiment(self, text: str) -> float:
        words = re.findall(r"[a-z]+", text.lower())
        score = sum(w in _POS for w in words) - sum(w in _NEG for w in words)
        return float(max(-1.0, min(1.0, score / 3)))


class _Model:
    name = "all-MiniLM-L6-v2"

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        from transformers import pipeline  # type: ignore

        self._st = SentenceTransformer(settings.EMBEDDING_MODEL, device="cpu")
        self._sent = pipeline("sentiment-analysis", model=settings.SENTIMENT_MODEL, device=-1)

    def encode(self, text: str) -> list[float]:
        return self._st.encode([text], normalize_embeddings=True)[0].tolist()

    def sentiment(self, text: str) -> float:
        out = self._sent(text[:512])[0]
        return float(out["score"] if out["label"].upper().startswith("POS") else -out["score"])


@lru_cache(maxsize=1)
def get_backend():
    mode = settings.EMBEDDINGS_BACKEND
    if mode == "hashing":
        return _Hashing()
    try:
        return _Model()
    except Exception as exc:
        if mode == "model":
            raise
        log.warning("embedding model unavailable (%s); using hashing fallback", exc)
        return _Hashing()


def backend_name() -> str:
    return get_backend().name


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


async def embed_and_cluster(session: AsyncSession, ticket_id: uuid.UUID) -> dict:
    """Runs in a background worker, never in the filing request."""
    ticket = await session.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
    if ticket is None or ticket.cluster_id is not None:
        return {"skipped": True}
    backend = get_backend()
    text = f"{ticket.title}. {ticket.description}"
    vec = np.array(backend.encode(text))
    ticket.embedding = vec.tolist()
    ticket.sentiment = backend.sentiment(text)

    # Nearest cluster in the same category by cosine distance (pgvector <=>).
    distance = ComplaintCluster.centroid.cosine_distance(vec.tolist())
    row = (
        await session.execute(
            select(ComplaintCluster, distance.label("d"))
            .where(ComplaintCluster.category == ticket.category)
            .order_by(distance)
            .limit(1)
            .with_for_update(of=ComplaintCluster)
        )
    ).first()

    if row is not None and row.d <= settings.CLUSTER_MAX_DISTANCE:
        cluster: ComplaintCluster = row[0]
        centroid = _normalise((np.array(cluster.centroid) * cluster.size + vec) / (cluster.size + 1))
        cluster.centroid = centroid.tolist()
        cluster.size += 1
        cluster.sentiment_sum += ticket.sentiment
        joined = True
    else:
        cluster = ComplaintCluster(
            id=uuid.uuid4(),
            category=ticket.category,
            centroid=vec.tolist(),
            size=1,
            sentiment_sum=ticket.sentiment,
            label=ticket.title[:200],
        )
        session.add(cluster)
        joined = False
    await session.flush()
    ticket.cluster_id = cluster.id

    if cluster.size >= settings.SYSTEMIC_CLUSTER_SIZE and not cluster.systemic_alerted:
        cluster.systemic_alerted = True
        from app.services.tickets import get_rule

        rule = await get_rule(session, ticket.category, 1)
        await notify(
            session,
            [ADMIN_CHANNEL, queue_channel(rule.handler_designation)],
            "systemic_issue",
            {
                "message": f"Systemic issue: {cluster.size} similar {ticket.category} complaints ('{cluster.label}')",
                "cluster_id": str(cluster.id),
                "category": ticket.category,
                "size": cluster.size,
            },
        )
    return {"cluster_id": str(cluster.id), "joined": joined, "size": cluster.size, "distance": row.d if row else None}
