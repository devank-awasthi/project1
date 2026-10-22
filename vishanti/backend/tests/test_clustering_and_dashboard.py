import importlib.util

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ComplaintCluster, Notification, Ticket
from app.services import dashboard, events
from app.services.dashboard import suppress, suppress_row
from app.services.embeddings import embed_and_cluster, get_backend
from tests.conftest import file


async def embed(tid):
    async with SessionLocal() as s:
        out = await embed_and_cluster(s, tid)
        await events.commit(s)
        return out


async def test_similar_complaints_cluster_and_flag_systemic_issue():
    texts = [
        ("Wi-Fi not working in hostel block C", "The Wi-Fi in hostel block C has been down since Monday."),
        ("Hostel block C Wi-Fi down again", "Wi-Fi not working in hostel block C, connection keeps dropping."),
        ("Block C hostel wifi not working", "Hostel block C Wi-Fi is down and not working for classes."),
    ]
    ids = [
        (await file(category="IT & Network", title=a, description=b + " Please fix it soon."))[0].id for a, b in texts
    ]
    other = (
        await file(
            category="IT & Network",
            title="Portal password reset email never arrives",
            description="The student portal never sends the password reset email to my inbox.",
        )
    )[0].id
    results = [await embed(i) for i in ids] + [await embed(other)]

    assert results[0]["joined"] is False
    assert results[1]["joined"] and results[2]["joined"]
    assert results[1]["cluster_id"] == results[0]["cluster_id"] == results[2]["cluster_id"]
    assert results[3]["cluster_id"] != results[0]["cluster_id"]
    async with SessionLocal() as s:
        cluster = await s.get(ComplaintCluster, __import__("uuid").UUID(results[0]["cluster_id"]))
        assert cluster.size == 3 and cluster.systemic_alerted
        alert = await s.scalar(select(Notification).where(Notification.event_type == "systemic_issue"))
        assert alert is not None and alert.payload["size"] == 3
        t = await s.get(Ticket, ids[0])
        assert t.embedding is not None and len(t.embedding) == 384


async def test_categories_are_clustered_separately():
    a = (
        await file(
            category="Hostel", title="Fan not working in room", description="The fan in my room is not working at all."
        )
    )[0]
    b = (
        await file(
            category="Mess", title="Fan not working in room", description="The fan in my room is not working at all."
        )
    )[0]
    ra, rb = await embed(a.id), await embed(b.id)
    assert ra["cluster_id"] != rb["cluster_id"]


@pytest.mark.skipif(importlib.util.find_spec("sentence_transformers") is None, reason="model not installed")
async def test_model_matches_paraphrases():
    """Only meaningful with the real model: different words, same problem."""
    backend = get_backend()
    import numpy as np

    a = np.array(backend.encode("Wi-Fi down in the hostel"))
    b = np.array(backend.encode("Internet not working in my hostel room"))
    c = np.array(backend.encode("Mess food is cold"))
    assert float(a @ b) > float(a @ c)


def test_small_counts_are_suppressed():
    assert suppress(0) == 0 and suppress(4) is None and suppress(5) == 5


def test_secondary_suppression_protects_lone_hidden_cell():
    # one hidden cell could be recovered from a published row total; hide the next smallest too
    assert suppress_row([12, 3, 7, 20]) == [12, None, None, 20]
    assert suppress_row([12, 3, 2, 20]) == [12, None, None, 20]
    assert suppress_row([12, 0, 9]) == [12, 0, 9]


async def test_dashboard_never_exposes_small_cells():
    for _ in range(6):
        await file(category="Hostel")
    for _ in range(2):
        await file(
            category="Library", title="Library closes early", description="The library closes early during exams week."
        )
    async with SessionLocal() as s:
        data = await dashboard.compute(s)
    rows = {r["category"]: r["cells"] for r in data["heatmap"]}
    assert rows["Hostel"][-1] == 6
    assert rows["Library"][-1] is None
    flat = [c for r in data["heatmap"] for c in r["cells"]]
    assert all(c is None or c == 0 or c >= 5 for c in flat)
