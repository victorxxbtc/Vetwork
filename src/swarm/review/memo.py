"""Assemble accepted findings into a scored, cited memo and hash it."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..agents.claims import SECTION_TITLES, SECTIONS, Finding

WEIGHTS = {"history": 0.30, "budget": 0.20, "milestones": 0.15, "treasury": 0.10, "risk": 0.25}
FUND_AT = 60
REVISE_AT = 45


def verdict_for(score: int) -> str:
    if score >= FUND_AT:
        return "FUND"
    if score >= REVISE_AT:
        return "REVISE"
    return "DECLINE"


# A verified duplicate filing caps the score: a council does not pay the same request twice.
DUPLICATE_CAP = 44


def combine(findings: dict[str, Finding]) -> int:
    used = {s: f for s, f in findings.items() if s in WEIGHTS}
    if not used:
        return 0
    total = sum(WEIGHTS[s] * 1.0 for s in used)
    score = round(sum(WEIGHTS[s] * f.score for s, f in used.items()) / total)
    risk = used.get("risk")
    if risk and any(c.kind in ("duplicate", "repeat_request") and c.status == "verified" for c in risk.claims):
        score = min(score, DUPLICATE_CAP)
    return score


def title_of(description: str) -> str:
    head = description.split("$$$$")[0].strip()
    line = next((ln.strip() for ln in head.splitlines() if ln.strip()), "") or "(no description)"
    return line if len(line) <= 120 else line[:117].rstrip() + "…"


def canonical(memo: dict[str, Any]) -> bytes:
    body = {k: v for k, v in memo.items() if k != "memo_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def memo_hash(memo: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(memo)).hexdigest()


def build_memo(
    *,
    dao: str,
    proposal: dict[str, Any],
    amount_display: str,
    accepted: dict[str, Finding],
    rejected: list[Finding],
    prices: dict[str, int],
    inference_usd: float,
    job_id: int,
) -> dict[str, Any]:
    score = combine(accepted)
    sources = sorted({s for f in list(accepted.values()) + rejected for c in f.claims for s in c.sources})
    sections = []
    for s in SECTIONS:
        f = accepted.get(s)
        sections.append(
            {
                "section": s,
                "title": SECTION_TITLES[s],
                "weight": WEIGHTS[s],
                "accepted": f is not None,
                **(f.to_dict() if f else {"agent": None, "score": None, "summary": "No agent passed review.", "claims": []}),
                "price_yocto": str(prices.get(s, 0)),
            }
        )
    memo = {
        "schema": "assay.memo/1",
        "job_id": job_id,
        "dao": dao,
        "proposal_id": proposal["id"],
        "proposer": proposal["proposer"],
        "receiver": proposal["receiver_id"],
        "amount": amount_display,
        "title": title_of(proposal["description"]),
        "score": score,
        "verdict": verdict_for(score),
        "sections": sections,
        "rejected_findings": [f.to_dict() for f in rejected],
        "sources": sources,
        "claims_total": sum(len(f.claims) for f in list(accepted.values()) + rejected),
        "claims_flagged": sum(c.status == "flagged" for f in list(accepted.values()) + rejected for c in f.claims),
        "cost": {"bids_yocto": str(sum(prices.values())), "inference_usd": round(inference_usd, 6)},
    }
    memo["memo_hash"] = memo_hash(memo)
    return memo
