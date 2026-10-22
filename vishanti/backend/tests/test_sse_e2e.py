"""Real server, real Redis pub/sub: live delivery and Last-Event-ID replay."""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def server():
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(PORT),
            "--no-access-log",
            "--log-level",
            "warning",
        ],
        env={**os.environ},
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    for _ in range(100):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield
    proc.terminate()
    proc.wait(5)


async def read_events(client, url, want: int, timeout=8.0):
    got = []
    async with client.stream("GET", url, timeout=None) as resp:
        assert resp.status_code == 200
        event = {}

        async def consume():
            async for line in resp.aiter_lines():
                if line.startswith("id:"):
                    event["id"] = int(line[3:].strip())
                elif line.startswith("event:"):
                    event["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    event["data"] = json.loads(line[5:].strip())
                elif line == "" and event:
                    got.append(dict(event))
                    event.clear()
                    if len([e for e in got if e.get("event") != "ready"]) >= want:
                        return

        await asyncio.wait_for(consume(), timeout)
    return [e for e in got if e.get("event") != "ready"]


async def test_live_events_and_replay(server):
    async with httpx.AsyncClient(base_url=BASE) as c:
        tok = (
            await c.post("/api/auth/login", json={"email": "student1@vishanti.edu", "password": "vishanti123"})
        ).json()
        h = {"Authorization": f"Bearer {tok['access_token']}"}
        key = (await c.post("/api/stream/key", headers=h)).json()["key"]

        listener = asyncio.create_task(read_events(c, f"/api/stream?key={key}", want=1))
        await asyncio.sleep(0.5)
        ticket = {
            "title": "Hostel fan broken",
            "description": "The fan in hostel room 12 is broken since last night.",
            "category": "Hostel",
            "priority": "medium",
        }
        r = await c.post("/api/tickets", json=ticket, headers={**h, "Idempotency-Key": str(uuid.uuid4())})
        tid = r.json()["ticket"]["id"]
        live = await listener
        assert live[0]["event"] == "ticket_filed" and live[0]["data"]["ticket_id"] == tid
        last_seen = live[0]["id"]

        # Disconnected now. The warden acts twice while we are away.
        wtok = (
            await c.post("/api/auth/login", json={"email": "warden@vishanti.edu", "password": "vishanti123"})
        ).json()
        wh = {"Authorization": f"Bearer {wtok['access_token']}"}
        for action in ("ASSIGN", "START_REVIEW"):
            assert (await c.post(f"/api/tickets/{tid}/actions", json={"action": action}, headers=wh)).status_code == 200

        missed = await read_events(c, f"/api/stream?key={key}&last_event_id={last_seen}", want=2)
        assert [e["data"]["status"] for e in missed] == ["ASSIGNED", "IN_REVIEW"]
        assert all(e["data"].get("replayed") for e in missed)
        assert missed[0]["id"] > last_seen
