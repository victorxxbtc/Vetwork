"""Hosting: the one-request replay used on Vercel matches the SSE stream, and the console picks it there."""

import json

from fastapi.testclient import TestClient

DAO = "marketing.sputnik-dao.near"


def sse_events(body: str):
    out = []
    for block in body.split("\n\n"):
        if block.startswith("event: swarm"):
            out.append(json.loads(block.split("data: ", 1)[1]))
    return out


def test_replay_endpoint_returns_the_same_run_as_the_stream():
    from swarm.web.app import app

    c = TestClient(app)
    replay = c.get(f"/api/events?dao={DAO}&id=1005").json()
    stream = sse_events(c.get(f"/events?dao={DAO}&id=1005&speed=0").text)
    assert replay["events"] == stream
    assert replay["memo_url"] == f"/memo/{DAO}/1005"
    assert replay["events"][-1]["type"] == "memo_recorded"


def test_console_uses_replay_on_vercel(monkeypatch):
    from swarm.web import app as webapp

    monkeypatch.delenv("SWARM_TRANSPORT", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    # the global is computed at import; recompute the way the app does
    t = webapp.os.environ.get("SWARM_TRANSPORT") or ("replay" if webapp.os.environ.get("VERCEL") else "sse")
    webapp.templates.env.globals["transport"] = t
    body = TestClient(webapp.app).get("/").text
    assert '"transport": "replay"' in body
    webapp.templates.env.globals["transport"] = "sse"
    assert '"transport": "sse"' in TestClient(webapp.app).get("/").text


def test_vercel_entrypoint_exposes_the_app():
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("vercel_index", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert TestClient(mod.app).get("/healthz").status_code == 200
