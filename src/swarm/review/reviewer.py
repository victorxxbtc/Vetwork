"""The reviewer: re-derives every claim from the record it cites.

It shares no code with the analysts on purpose. A section is accepted (and
its agent paid) only if every claim in it checks out; otherwise the section
goes to the runner-up bidder.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any

from ..agents.claims import Claim, Finding
from ..context import DECIDED, ReviewContext, source_id

DAY = 86_400 * 10**9
_URL = re.compile(r"https?://[^\s<>\"')\]$]+")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class SectionVerdict:
    section: str
    agent: str
    accepted: bool
    verified: int
    flagged: int


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _flag(c: Claim, reason: str) -> Claim:
    c.status, c.reason = "flagged", reason
    return c


def _ok(c: Claim) -> Claim:
    c.status, c.reason = "verified", ""
    return c


def check_claim(c: Claim, ctx: ReviewContext) -> Claim:
    src = ctx.sources
    for sid in c.sources:
        if sid not in src:
            return _flag(c, f"{sid} did not exist at submission time")
    cited = [src[s] for s in c.sources]
    t = ctx.target
    p = c.params

    if c.kind == "count":
        statuses = p.get("status")
        if statuses:
            for r in cited:
                if "status" not in r:
                    return _flag(c, f"#{r['id']} was still being voted on at submission time")
            pool = [r for r in ctx.history if r.get("status") in statuses]
        else:
            pool = list(ctx.history)
        if p.get("any_party"):
            pool = [r for r in pool if r["token_id"] == p.get("token")]
        else:
            pool = [r for r in pool if r["proposer"] == p.get("proposer") or (p.get("receiver") and r["receiver_id"] == p["receiver"])]
        expected = {source_id(ctx.dao, r["id"]) for r in pool}
        if set(c.sources) != expected:
            return _flag(c, f"cites {len(c.sources)} records; the record at submission shows {len(expected)}")
        if c.value != len(expected):
            return _flag(c, f"states {c.value}; record shows {len(expected)}")
        return _ok(c)

    if c.kind in ("median_amount", "sum_amount"):
        for r in cited:
            if r.get("status") != "Approved":
                return _flag(c, f"#{r['id']} was not an approved transfer at submission time")
            if r["token_id"] != p.get("token"):
                return _flag(c, f"#{r['id']} is in a different token")
        amounts = [int(r["amount"]) for r in cited]
        if c.kind == "median_amount":
            got = int(statistics.median(amounts)) if amounts else 0
        else:
            win = int(p.get("window_days", 0)) * DAY
            expected = {
                source_id(ctx.dao, r["id"])
                for r in ctx.history
                if r.get("status") == "Approved" and r["token_id"] == p.get("token") and r["submission_time"] >= ctx.cutoff_ns - win
            }
            if set(c.sources) != expected:
                return _flag(c, f"window holds {len(expected)} approved transfers, claim cites {len(c.sources)}")
            got = sum(amounts)
        if str(got) != str(c.value):
            return _flag(c, "amount does not match the cited records")
        return _ok(c)

    if c.kind == "budget_ratio":
        med = int(p["median"])
        got = round(int(t["amount"]) / med, 2) if med else 0.0
        return _ok(c) if abs(got - float(c.value)) < 0.006 else _flag(c, f"ratio is {got}, not {c.value}")

    if c.kind == "quote":
        return _ok(c) if str(p.get("quote", "")) in t["description"] else _flag(c, "quoted text is not in the proposal")

    if c.kind == "absent":
        return _flag(c, "the proposal does contain a link") if _URL.search(t["description"]) else _ok(c)

    if c.kind == "duplicate":
        for r in cited:
            if r["proposer"] != t["proposer"] or _norm(r["description"]) != _norm(t["description"]):
                return _flag(c, f"#{r['id']} is not the same text from the same proposer")
        return _ok(c) if c.value == len(cited) else _flag(c, "count does not match citations")

    if c.kind == "repeat_request":
        for r in cited:
            same = (r["proposer"], r["receiver_id"], r["token_id"], r["amount"]) == (
                t["proposer"], t["receiver_id"], t["token_id"], t["amount"])
            if not same or r["submission_time"] < ctx.cutoff_ns - 30 * DAY:
                return _flag(c, f"#{r['id']} is not the same request within 30 days")
        return _ok(c) if c.value == len(cited) >= 1 else _flag(c, "count does not match citations")

    if c.kind == "burst":
        for r in cited:
            if r["proposer"] != t["proposer"] or r["submission_time"] < ctx.cutoff_ns - DAY:
                return _flag(c, f"#{r['id']} is outside the 24 h window or from someone else")
        return _ok(c) if c.value == len(cited) >= 3 else _flag(c, "fewer than three requests in 24 h")

    if c.kind == "target_field":
        f = p.get("field")
        if f == "text_length":
            got: Any = len(_URL.sub("", t["description"]).replace("$$$$", "").strip())
        elif f == "implicit_receiver":
            got = bool(_HEX64.match(t["receiver_id"]))
        elif f == "receiver_id":
            got = t["receiver_id"]
        else:
            return _flag(c, f"unknown field {f}")
        return _ok(c) if got == c.value else _flag(c, f"record shows {got!r}")

    return _flag(c, f"unknown claim kind {c.kind}")


def review_finding(f: Finding, ctx: ReviewContext) -> SectionVerdict:
    for c in f.claims:
        check_claim(c, ctx)
    flagged = sum(c.status == "flagged" for c in f.claims)
    verified = sum(c.status == "verified" for c in f.claims)
    return SectionVerdict(f.section, f.agent, accepted=bool(f.claims) and flagged == 0, verified=verified, flagged=flagged)


__all__ = ["check_claim", "review_finding", "SectionVerdict", "DECIDED"]
