"""The MCP server, driven over real stdio as Claude would drive it."""

import json
import subprocess
import sys


def session(*msgs):
    lines = "\n".join(json.dumps(m) for m in msgs) + "\n"
    p = subprocess.run([sys.executable, "-m", "swarm.mcp_server"], input=lines, capture_output=True, text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin"})
    assert p.returncode == 0, p.stderr
    return [json.loads(l) for l in p.stdout.splitlines()]


def call(i, name, **args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


def text(r):
    return json.loads(r["result"]["content"][0]["text"])


def test_a_dao_claude_orders_and_reads_a_review():
    out = session(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        call(3, "request_review", dao="marketing.sputnik-dao.near", proposal_id=1005),
        call(5, "get_memo", dao="marketing.sputnik-dao.near", proposal_id=1005),
        call(6, "agent_reputation"),
        call(7, "agent_reputation", events_file="docs/sandbox-events.jsonl"),
        call(8, "request_review", dao="nosuch.sputnik-dao.near", proposal_id=1),
        call(9, "list_agents"),
    )
    by_id = {m["id"]: m for m in out}
    assert len(out) == 8  # the notification gets no reply
    assert by_id[1]["result"]["serverInfo"]["name"] == "vetwork"
    names = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert names == {"request_review", "review_status", "get_memo", "agent_reputation", "list_agents"}
    r = text(by_id[3])
    assert r["verdict"] in {"FUND", "REVISE", "DECLINE"} and len(r["memo_hash"]) == 64
    assert "demo" in r["mode"]
    memo = text(by_id[5])
    assert memo["memo_hash"] == r["memo_hash"] and memo["sections"]
    rep = text(by_id[6])["agents"]
    assert rep and all(v["accepted"] + v["rejected"] > 0 for v in rep.values())
    chain = text(by_id[7])["agents"]
    assert chain["ledger.vetwork.test.near"]["slashed"] == 1
    assert by_id[8]["result"]["isError"] and "not cached" in by_id[8]["result"]["content"][0]["text"]
    assert len(text(by_id[9])) >= 10


def test_status_and_errors():
    out = session(call(1, "request_review", dao="marketing.sputnik-dao.near", proposal_id=1019, bounty_near=1.5),
                  {"jsonrpc": "2.0", "id": 2, "method": "nope"},
                  call(3, "request_review", dao="marketing.sputnik-dao.near", proposal_id=1019, bounty_near=0.01))
    job = text(out[0])["job_id"]
    assert out[1]["error"]["code"] == -32601
    assert out[2]["result"]["isError"]
    st = session(call(1, "request_review", dao="marketing.sputnik-dao.near", proposal_id=1019), call(2, "review_status", job_id=0))
    assert text(st[1])["status"] == "unknown"
    assert isinstance(job, int)


def test_review_status_reports_payouts():
    from swarm.mcp_server import Vetwork

    app = Vetwork()
    r = app.request_review("marketing.sputnik-dao.near", 1005)
    st = app.review_status(r["job_id"])
    assert st["paid"] and st["steps"]["bid_committed"] >= 5
    assert any(s["agent"].startswith("quickcheck") for s in st["sent_back"])
