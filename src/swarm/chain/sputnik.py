"""Read Sputnik DAO v2 proposals from mainnet and cache them as compact JSON.

Only Transfer proposals are kept: those are the grant / payout requests a
treasury actually decides on. Outcomes (status, votes) are stored, but the
agents never see the outcome of the proposal they review (see
`swarm.context.build_context`).
"""

from __future__ import annotations

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .rpc import NearRpc

PAGE = 50
DESC_LIMIT = 4000


def _compact(p: dict[str, Any]) -> dict[str, Any] | None:
    kind = p.get("kind")
    if not isinstance(kind, dict) or "Transfer" not in kind:
        return None
    t = kind["Transfer"]
    return {
        "id": p["id"],
        "proposer": p["proposer"],
        "description": (p.get("description") or "")[:DESC_LIMIT],
        "token_id": t.get("token_id") or "",
        "receiver_id": t["receiver_id"],
        "amount": str(t["amount"]),
        "submission_time": int(p["submission_time"]),
        "status": p["status"],
        "vote_counts": p.get("vote_counts", {}),
    }


def fetch_dao(dao: str, rpc: NearRpc | None = None) -> dict[str, Any]:
    rpc = rpc or NearRpc()
    last = rpc.view(dao, "get_last_proposal_id")
    policy = rpc.view(dao, "get_policy")
    pages = list(range(0, last, PAGE))

    def page(start: int) -> list[dict[str, Any]]:
        return rpc.view(dao, "get_proposals", {"from_index": start, "limit": PAGE})

    with ThreadPoolExecutor(max_workers=2) as pool:
        raw = [p for chunk in pool.map(page, pages) for p in chunk]
    proposals = [c for c in (_compact(p) for p in raw) if c]
    return {
        "dao": dao,
        "last_proposal_id": last,
        "proposal_period_ns": int(policy.get("proposal_period", "604800000000000")),
        "proposals": sorted(proposals, key=lambda p: p["id"]),
    }


def save(snapshot: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot['dao']}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(snapshot, fh, separators=(",", ":"), sort_keys=True)
    return path


def load(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def fetch_token_decimals(token_ids: set[str], rpc: NearRpc | None = None) -> dict[str, int]:
    rpc = rpc or NearRpc()
    out: dict[str, int] = {"": 24}
    for tok in sorted(token_ids - {""}):
        try:
            out[tok] = int(rpc.view(tok, "ft_metadata")["decimals"])
        except Exception:  # token contract gone or not an FT: leave unknown
            out[tok] = -1
    return out
