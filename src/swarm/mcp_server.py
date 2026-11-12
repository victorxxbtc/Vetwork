"""Vetwork as an MCP server (stdio, JSON-RPC 2.0), so a DAO's Claude can order a review.

    claude mcp add vetwork -- uv run --directory /path/to/vetwork python -m swarm.mcp_server

Tools: request_review, review_status, get_memo, agent_reputation, list_agents.

Reviews run the same engine as the console, in demo mode (scripted agents, in-memory
ledger that follows the contract's rules) unless NEAR_AI_API_KEY is set. Proposals are
read from the cached mainnet snapshots; with fetch=true a DAO that is not cached is read
from mainnet RPC (view calls only). When SWARM_CONTRACT_ID is set, request_review also
returns the exact `near` CLI call that posts the job on chain; it never signs anything.

No SDK dependency: the protocol subset needed here (initialize, tools/list, tools/call,
ping) is small enough to implement directly and test end to end.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .agents.llm import client_from_env
from .engine import Swarm, run_job
from .evaluate import DATA, load_decimals
from .ledger import YOCTO, near
from .reputation import recompute

PROTOCOL = "2024-11-05"


class Vetwork:
    def __init__(self) -> None:
        self.swarm = Swarm.seeded()
        self.jobs: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.decimals = load_decimals()

    def _snapshot(self, dao: str, fetch: bool) -> dict[str, Any]:
        from .chain.sputnik import fetch_dao, load, save

        p = DATA / "daos" / f"{dao}.json.gz"
        if p.exists():
            return load(p)
        if not fetch:
            cached = sorted(x.name.removesuffix(".json.gz") for x in (DATA / "daos").glob("*.json.gz"))
            raise ValueError(f"{dao} is not cached. Cached DAOs: {', '.join(cached)}. Pass fetch=true to read it from mainnet RPC.")
        snap = fetch_dao(dao)
        save(snap, DATA / "daos")
        return snap

    # ---------------------------------------------------------------- tools
    def request_review(self, dao: str, proposal_id: int, bounty_near: float = 2.0, fetch: bool = False) -> dict[str, Any]:
        if not 0.5 <= float(bounty_near) <= 100:
            raise ValueError("bounty_near must be between 0.5 and 100")
        snap = self._snapshot(dao, fetch)
        if not any(p["id"] == int(proposal_id) for p in snap["proposals"]):
            raise ValueError(f"{dao} has no Transfer proposal #{proposal_id}")
        job_id = int.from_bytes(hashlib.sha256(f"{dao}#{proposal_id}#{len(self.jobs)}".encode()).digest()[:3], "big")
        res = run_job(self.swarm, snap, int(proposal_id), self.decimals, job_id=job_id, bounty=near(float(bounty_near)),
                      llm=client_from_env())
        m = res.memo
        self.events.extend(res.events)
        self.jobs[job_id] = {"dao": dao, "proposal_id": int(proposal_id), "memo": m, "events": res.events, "backend": res.backend}
        out: dict[str, Any] = {
            "job_id": job_id, "status": "reviewed", "verdict": m["verdict"], "score": m["score"],
            "memo_hash": m["memo_hash"], "claims": m["claims_total"], "claims_flagged": m["claims_flagged"],
            "mode": "NEAR AI" if res.backend == "nearai" else "demo (scripted agents, in-memory ledger)",
            "memo_url": f"/memo/{dao}/{proposal_id}",
        }
        contract = os.environ.get("SWARM_CONTRACT_ID")
        if contract:
            args = json.dumps({"dao": dao, "proposal_id": int(proposal_id), "description": "Vetwork review", "commit_secs": 600, "reveal_secs": 600})
            out["post_on_chain"] = (f"near contract call-function as-transaction {contract} post_job json-args '{args}' "
                                    f"prepaid-gas '30 Tgas' attached-deposit '{bounty_near} NEAR' sign-as <your-account> network-config testnet")
        return out

    def review_status(self, job_id: int) -> dict[str, Any]:
        j = self.jobs.get(int(job_id))
        if not j:
            return {"job_id": job_id, "status": "unknown", "known_jobs": sorted(self.jobs)}
        steps = [e["type"] for e in j["events"]]
        paid = [{"agent": e["agent"], "section": e["section"], "near": int(e["amount"]) / YOCTO} for e in j["events"] if e["type"] == "payout"]
        sent_back = [{"agent": e["agent"], "section": e["section"], "why": e["flags"][:1]} for e in j["events"] if e["type"] == "reviewed" and not e["accepted"]]
        return {"job_id": job_id, "status": "reviewed", "dao": j["dao"], "proposal_id": j["proposal_id"],
                "steps": {k: steps.count(k) for k in dict.fromkeys(steps)}, "paid": paid, "sent_back": sent_back,
                "verdict": j["memo"]["verdict"], "score": j["memo"]["score"], "memo_hash": j["memo"]["memo_hash"]}

    def get_memo(self, dao: str, proposal_id: int) -> dict[str, Any]:
        for j in reversed(list(self.jobs.values())):
            if j["dao"] == dao and j["proposal_id"] == int(proposal_id):
                return j["memo"]
        raise ValueError(f"no review of {dao}#{proposal_id} yet; call request_review first")

    def agent_reputation(self, agent: str | None = None, events_file: str | None = None) -> dict[str, Any]:
        if events_file:
            from .reputation import from_file

            reps, source = from_file(Path(events_file)), f"chain events in {events_file}"
        else:
            reps, source = recompute(self.events), "reviews ordered in this session"
        rows = {a: r.to_dict() for a, r in reps.items() if agent is None or a == agent or a.split(".")[0] == agent}
        return {"source": source, "formula": "(accepted + 1) / (accepted + rejected + 3 * slashed + 2)", "agents": rows}

    def list_agents(self) -> list[dict[str, Any]]:
        out = []
        for a in self.swarm.agents:
            acct = a.account(self.swarm.account)
            out.append({"agent": acct, "section": a.section, "model": a.model, "knowledge_base": a.knowledge_base,
                        "balance_near": round(self.swarm.ledger.agents[acct].balance / YOCTO, 4)})
        return out


TOOLS: list[dict[str, Any]] = [
    {"name": "request_review", "description": "Order a cited review memo for a Sputnik DAO Transfer proposal. Specialist agents bid sealed for five sections, a blind reviewer checks every claim against the cited record, and the memo hash is recorded. Returns verdict (FUND/REVISE/DECLINE), score and memo hash.",
     "inputSchema": {"type": "object", "properties": {
         "dao": {"type": "string", "description": "e.g. marketing.sputnik-dao.near"},
         "proposal_id": {"type": "integer"},
         "bounty_near": {"type": "number", "default": 2.0, "description": "bounty escrowed for the agents, 0.5 to 100 NEAR"},
         "fetch": {"type": "boolean", "default": False, "description": "read an uncached DAO from mainnet RPC (view calls only)"}},
         "required": ["dao", "proposal_id"]}},
    {"name": "review_status", "description": "Status of a review job: which agents were paid, which sections were sent back and why.",
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "integer"}}, "required": ["job_id"]}},
    {"name": "get_memo", "description": "The full memo for a reviewed proposal: verdict, section scores, every claim with its sources and reviewer status.",
     "inputSchema": {"type": "object", "properties": {"dao": {"type": "string"}, "proposal_id": {"type": "integer"}}, "required": ["dao", "proposal_id"]}},
    {"name": "agent_reputation", "description": "Agent reputation recomputed from history (accepted, rejected, slashed). Pass events_file to recompute from a contract EVENT_JSON log.",
     "inputSchema": {"type": "object", "properties": {"agent": {"type": "string"}, "events_file": {"type": "string"}}}},
    {"name": "list_agents", "description": "Agents in the swarm with their section, model, knowledge base and funded balance.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def handle(app: Vetwork, msg: dict[str, Any]) -> dict[str, Any] | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification (e.g. notifications/initialized)
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "vetwork", "version": "0.1.0"}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        fn: Callable[..., Any] | None = getattr(app, name, None) if name in {t["name"] for t in TOOLS} else None
        if fn is None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            result = fn(**args)
            return ok({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}], "isError": False})
        except (ValueError, TypeError) as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    app = Vetwork()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            out = handle(app, msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
