"""HTTP layer: auth, idempotency, anonymous access, role guards, uploads,
access logging, public dashboard."""

import io
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.main import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def login(client, email, password="vishanti123"):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


TICKET = {
    "title": "Water leakage in hostel room 214",
    "description": "Water has been leaking from the ceiling for a week, the mattress is wet.",
    "category": "Hostel",
    "priority": "high",
}


async def test_register_login_refresh_me(client):
    r = await client.post(
        "/api/auth/register",
        json={
            "email": "new@vishanti.edu",
            "password": "longpassword",
            "full_name": "New Student",
            "reg_no": "20259999",
        },
    )
    assert r.status_code == 201
    refresh = r.json()["refresh_token"]
    r = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["user"]["role"] == "student"
    bad = await client.post("/api/auth/login", json={"email": "new@vishanti.edu", "password": "wrong-password"})
    assert bad.status_code == 401


async def test_refresh_token_cannot_be_used_as_access_token(client):
    r = await client.post("/api/auth/login", json={"email": "student1@vishanti.edu", "password": "vishanti123"})
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['refresh_token']}"})
    assert resp.status_code == 401


async def test_filing_is_idempotent(client):
    h = await login(client, "student1@vishanti.edu")
    key = str(uuid.uuid4())
    first = await client.post("/api/tickets", json=TICKET, headers={**h, "Idempotency-Key": key})
    second = await client.post("/api/tickets", json=TICKET, headers={**h, "Idempotency-Key": key})
    assert first.status_code == 201 and second.status_code == 200
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.json()["ticket"]["id"] == second.json()["ticket"]["id"]
    mine = await client.get("/api/tickets/mine", headers=h)
    assert len(mine.json()) == 1


async def test_anonymous_filing_and_tracking(client):
    key = str(uuid.uuid4())
    r = await client.post("/api/tickets", json={**TICKET, "anonymous": True}, headers={"Idempotency-Key": key})
    assert r.status_code == 201
    token = r.json()["anonymous_token"]
    tid = r.json()["ticket"]["id"]
    assert r.json()["ticket"]["anonymous"] is True

    # a retry after a dropped connection gets the same token back
    retry = await client.post("/api/tickets", json={**TICKET, "anonymous": True}, headers={"Idempotency-Key": key})
    assert retry.json()["anonymous_token"] == token

    tracked = await client.get("/api/anon/ticket", headers={"X-Anon-Token": token})
    assert tracked.status_code == 200 and tracked.json()["id"] == tid
    assert (await client.get(f"/api/tickets/{tid}/ledger", headers={"X-Anon-Token": token})).status_code == 200
    wrong = await client.get("/api/anon/ticket", headers={"X-Anon-Token": "vsh_" + "x" * 43})
    assert wrong.status_code == 404


async def test_non_anonymous_filing_requires_login(client):
    r = await client.post("/api/tickets", json=TICKET, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 401


async def test_officials_flow_and_access_log(client):
    student = await login(client, "student1@vishanti.edu")
    r = await client.post("/api/tickets", json=TICKET, headers={**student, "Idempotency-Key": str(uuid.uuid4())})
    tid = r.json()["ticket"]["id"]

    warden = await login(client, "warden@vishanti.edu")
    q = await client.get("/api/official/queue", headers=warden)
    assert tid in [t["id"] for t in q.json()["tickets"]]
    for action, note in (("ASSIGN", None), ("START_REVIEW", None), ("RESOLVE", "Fixed the pipe")):
        resp = await client.post(f"/api/tickets/{tid}/actions", json={"action": action, "note": note}, headers=warden)
        assert resp.status_code == 200, resp.text
    await client.get(f"/api/tickets/{tid}", headers=warden)

    log = await client.get(f"/api/tickets/{tid}/access-log", headers=student)
    assert any(e["designation"] == "Warden" and e["action"] == "view" for e in log.json())

    done = await client.post(f"/api/tickets/{tid}/actions", json={"action": "CONFIRM"}, headers=student)
    assert done.json()["status"] == "CLOSED"
    v = await client.get(f"/api/tickets/{tid}/verify", headers=student)
    assert v.json()["ok"] is True


async def test_other_people_cannot_see_a_ticket(client):
    s1 = await login(client, "student1@vishanti.edu")
    r = await client.post("/api/tickets", json=TICKET, headers={**s1, "Idempotency-Key": str(uuid.uuid4())})
    tid = r.json()["ticket"]["id"]
    s2 = await login(client, "student2@vishanti.edu")
    bursar = await login(client, "bursar@vishanti.edu")  # not on the Hostel ladder
    assert (await client.get(f"/api/tickets/{tid}", headers=s2)).status_code == 404
    assert (await client.get(f"/api/tickets/{tid}", headers=bursar)).status_code == 404


async def test_system_only_actions_cannot_be_called_over_http(client):
    s1 = await login(client, "student1@vishanti.edu")
    r = await client.post("/api/tickets", json=TICKET, headers={**s1, "Idempotency-Key": str(uuid.uuid4())})
    tid = r.json()["ticket"]["id"]
    resp = await client.post(f"/api/tickets/{tid}/actions", json={"action": "SLA_ESCALATE"}, headers=s1)
    assert resp.status_code == 422


async def test_admin_routes_need_admin(client):
    s1 = await login(client, "student1@vishanti.edu")
    warden = await login(client, "warden@vishanti.edu")
    admin = await login(client, "admin@vishanti.edu")
    for h, code in ((s1, 403), (warden, 403), (admin, 200)):
        assert (await client.get("/api/admin/integrity", headers=h)).status_code == code
    assert (await client.post("/api/admin/sweep")).status_code == 401


async def test_rule_change_is_logged(client):
    admin = await login(client, "admin@vishanti.edu")
    r = await client.put(
        "/api/admin/rules",
        json={"category": "Hostel", "level": 1, "handler_designation": "Warden", "base_sla_hours": 24},
        headers=admin,
    )
    assert r.status_code == 200 and r.json()["base_sla_hours"] == 24
    logs = await client.get("/api/admin/access-logs", headers=admin)
    change = next(e for e in logs.json() if e["action"] == "rule_change")
    assert change["detail"]["before"]["base_sla_hours"] == 48


def _jpeg_with_gps() -> bytes:
    img = Image.new("RGB", (32, 32), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SecretPhoneMaker"  # Make
    exif[0x8825] = {1: "N", 2: (26.0, 51.0, 0.0)}  # GPS IFD
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


async def test_upload_pipeline_strips_metadata_and_hashes(client):
    raw = _jpeg_with_gps()
    assert b"SecretPhoneMaker" in raw
    p = (
        await client.post("/api/uploads", json={"filename": "leak.jpg", "content_type": "image/jpeg", "size": len(raw)})
    ).json()
    assert (await client.put(p["put_url"].replace("http://localhost:8000", ""), content=raw)).status_code == 204
    fin = (await client.post(f"/api/uploads/{p['upload_id']}/finalize")).json()
    assert len(fin["sha256"]) == 64

    s1 = await login(client, "student1@vishanti.edu")
    r = await client.post(
        "/api/tickets",
        json={**TICKET, "upload_ids": [p["upload_id"]]},
        headers={**s1, "Idempotency-Key": str(uuid.uuid4())},
    )
    ticket = r.json()["ticket"]
    att = ticket["attachments"][0]
    assert att["sha256"] == fin["sha256"]
    ledger = (await client.get(f"/api/tickets/{ticket['id']}/ledger", headers=s1)).json()
    assert ledger["entries"][0]["payload"]["attachments"][0]["sha256"] == fin["sha256"]

    dl = await client.get(f"/api/tickets/{ticket['id']}/attachments/{att['id']}", headers=s1)
    assert dl.status_code == 200
    assert b"SecretPhoneMaker" not in dl.content
    assert Image.open(io.BytesIO(dl.content)).getexif().get(0x8825) is None
    import hashlib

    assert hashlib.sha256(dl.content).hexdigest() == fin["sha256"]


async def test_upload_with_fake_type_is_rejected(client):
    body = b"#!/bin/sh\necho pwned\n"
    p = (
        await client.post("/api/uploads", json={"filename": "x.png", "content_type": "image/png", "size": len(body)})
    ).json()
    await client.put(p["put_url"].replace("http://localhost:8000", ""), content=body)
    assert (await client.post(f"/api/uploads/{p['upload_id']}/finalize")).status_code == 415


async def test_upload_type_and_size_limits(client):
    assert (
        await client.post(
            "/api/uploads", json={"filename": "a.exe", "content_type": "application/x-msdownload", "size": 10}
        )
    ).status_code == 415
    assert (
        await client.post(
            "/api/uploads", json={"filename": "a.pdf", "content_type": "application/pdf", "size": 50_000_000}
        )
    ).status_code == 413


async def test_public_dashboard_and_meta(client):
    meta = (await client.get("/api/public/meta")).json()
    assert "Hostel" in meta["categories"] and meta["ladders"]["Hostel"][0]["designation"] == "Warden"
    dash = await client.get("/api/public/dashboard")
    assert dash.status_code == 200 and "heatmap" in dash.json()


async def test_stream_key_requires_identity(client):
    assert (await client.post("/api/stream/key")).status_code == 401
    s1 = await login(client, "student1@vishanti.edu")
    r = await client.post("/api/stream/key", headers=s1)
    assert r.status_code == 200 and len(r.json()["key"]) >= 24
    assert (await client.get("/api/stream", params={"key": "x" * 32})).status_code == 401
