"""The five specialist analyses. Each returns a Finding made of checkable claims.

These are the deterministic "tools" every agent runs. An agent's prompt and
model only change the prose summary (when NEAR AI is enabled); its knowledge
base and parameters change what it looks at, which is where agents differ and
where the reviewer earns its keep.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from ..context import DECIDED, ReviewContext, source_id
from .claims import Claim, Finding

DAY_NS = 86_400 * 10**9
URL_RE = re.compile(r"https?://[^\s<>\"')\]$]+")
DATE_RE = re.compile(
    r"\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d\d)?"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?(?:\s+20\d\d)?"
    r"|20\d\d-\d\d-\d\d|Q[1-4]\s?20\d\d|\d{1,2}/\d{1,2}/20\d\d)",
    re.IGNORECASE,
)
QTY_RE = re.compile(
    r"\b\d[\d,.]*\s?(?:k\s)?(?:\+\s)?(?:users|participants|attendees|views|impressions|followers|members|"
    r"articles|videos|posts|episodes|events|workshops|sessions|students|tweets|threads|downloads|installs|"
    r"transactions|wallets|accounts|hours|weeks|days|months|issues|newsletters|streams|%)",
    re.IGNORECASE,
)
DELIVER_RE = re.compile(r"\b(deliverables?|milestones?|kpis?|report|will deliver|we will|timeline|scope)\b", re.I)
IMPLICIT_RE = re.compile(r"^[0-9a-f]{64}$")


def fmt_amount(amount: int | str, decimals: int, token: str = "") -> str:
    a = int(amount)
    unit = "NEAR" if token == "" else (token.split(".")[0][:10] if decimals >= 0 else token[:10])
    if token.startswith(("usdt", "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1", "a0b86991")):
        unit = "USDT" if token.startswith("usdt") else "USDC"
    if decimals < 0:
        return f"{a} raw {unit}"
    v = a / 10**decimals
    return f"{v:,.2f} {unit}" if v < 1000 else f"{v:,.0f} {unit}"


def _ids(dao: str, ps: list[dict[str, Any]]) -> list[str]:
    return [source_id(dao, p["id"]) for p in ps]


def _parties(ctx: ReviewContext) -> tuple[str, str]:
    return ctx.target["proposer"], ctx.target["receiver_id"]


# ---------------------------------------------------------------- history
def history(ctx: ReviewContext, kb: dict[str, Any] | None = None, *, look_ahead: bool = False) -> Finding:
    """Past grants by the same proposer or receiver, and how the DAO voted on them.

    `look_ahead=True` is the naive variant: it reads the agent's own knowledge
    base (a current full export of the DAO) instead of the dated context, so it
    can see outcomes that did not exist yet. The reviewer catches this.
    """
    proposer, receiver = _parties(ctx)
    if look_ahead and kb and "export" in kb:
        pool = [p for p in kb["export"]["proposals"] if p["id"] != ctx.target["id"]]
    else:
        pool = ctx.decided()
    mine = [p for p in pool if p["proposer"] == proposer or p["receiver_id"] == receiver]
    approved = [p for p in mine if p.get("status") == "Approved"]
    rejected = [p for p in mine if p.get("status") in ("Rejected", "Removed")]
    claims = [
        Claim(
            text=f"{len(approved)} earlier transfer proposals from this proposer or to this receiver were approved.",
            kind="count",
            value=len(approved),
            sources=_ids(ctx.dao, approved),
            params={"proposer": proposer, "receiver": receiver, "status": ["Approved"]},
        ),
        Claim(
            text=f"{len(rejected)} were rejected or removed.",
            kind="count",
            value=len(rejected),
            sources=_ids(ctx.dao, rejected),
            params={"proposer": proposer, "receiver": receiver, "status": ["Rejected", "Removed"]},
        ),
    ]
    a, r = len(approved), len(rejected)
    if a + r == 0:
        score, summary = 50, "No decided track record in this DAO before submission: a first request."
    else:
        score = round(100 * (a + 1) / (a + r + 2))
        if a >= 3 and r == 0:
            score = max(score, 85)
        summary = f"{a} approved and {r} rejected requests before this one."
    return Finding("history", "", max(5, min(95, score)), summary, claims)


# ---------------------------------------------------------------- budget
def budget(ctx: ReviewContext, kb: dict[str, Any] | None = None, *, window_days: int = 365) -> Finding:
    t = ctx.target
    amount = int(t["amount"])
    approved = ctx.same_token([p for p in ctx.decided() if p["status"] == "Approved"])
    recent = [p for p in approved if p["submission_time"] >= ctx.cutoff_ns - window_days * DAY_NS]
    peers = recent if len(recent) >= 3 else approved
    if len(peers) < 3:
        return Finding(
            "budget",
            "",
            50,
            "Fewer than three approved peer requests in this token; no benchmark.",
            [
                Claim(
                    text=f"Only {len(peers)} approved peer requests in the same token before submission.",
                    kind="count",
                    value=len(peers),
                    sources=_ids(ctx.dao, peers),
                    params={"token": t["token_id"], "status": ["Approved"], "any_party": True},
                )
            ],
        )
    med = int(statistics.median(int(p["amount"]) for p in peers))
    ratio = round(amount / med, 2) if med else 0.0
    claims = [
        Claim(
            text=f"Median approved peer request: {fmt_amount(med, ctx.decimals, t['token_id'])} across {len(peers)} proposals.",
            kind="median_amount",
            value=str(med),
            sources=_ids(ctx.dao, peers),
            params={"token": t["token_id"]},
        ),
        Claim(
            text=f"This request ({fmt_amount(amount, ctx.decimals, t['token_id'])}) is {ratio:.2f}x the peer median.",
            kind="budget_ratio",
            value=ratio,
            sources=[ctx.target_id],
            params={"median": str(med)},
        ),
    ]
    if ratio <= 0.5:
        score = 85
    elif ratio <= 1.0:
        score = 78
    elif ratio <= 1.5:
        score = 65
    elif ratio <= 2.5:
        score = 52
    elif ratio <= 5:
        score = 38
    else:
        score = 25
    word = "below" if ratio < 0.95 else ("in line with" if ratio <= 1.05 else "above")
    return Finding("budget", "", score, f"Request is {ratio:.2f}x the median, {word} what this DAO funds.", claims)


# ---------------------------------------------------------------- milestones
def _first(regex: re.Pattern[str], text: str, n: int) -> list[str]:
    seen: list[str] = []
    for m in regex.finditer(text):
        s = m.group(0).strip().rstrip(".,;:")
        if s and s not in seen:
            seen.append(s)
        if len(seen) >= n:
            break
    return seen


def milestones(ctx: ReviewContext, kb: dict[str, Any] | None = None) -> Finding:
    desc = ctx.target["description"]
    urls = _first(URL_RE, desc, 2)
    dates = _first(DATE_RE, desc, 1)
    qtys = _first(QTY_RE, desc, 2)
    deliver = _first(DELIVER_RE, desc, 1)
    tid = ctx.target_id
    claims: list[Claim] = []
    for u in urls:
        claims.append(Claim(f"Links a checkable artefact: {u}", "quote", u, [tid], {"quote": u}))
    for d in dates:
        claims.append(Claim(f"States a date: “{d}”.", "quote", d, [tid], {"quote": d}))
    for q in qtys:
        claims.append(Claim(f"States a measurable target: “{q}”.", "quote", q, [tid], {"quote": q}))
    if not urls:
        claims.append(Claim("No link to a plan, report or deliverable.", "absent", "url", [tid], {"pattern": "url"}))
    score = 25 + 25 * bool(urls) + 15 * bool(dates) + 15 * bool(qtys) + 10 * bool(deliver) + 10 * (len(desc) >= 280)
    parts = []
    if urls:
        parts.append(f"{len(urls)} link{'s' if len(urls) > 1 else ''}")
    if dates:
        parts.append("a date")
    if qtys:
        parts.append("measurable targets")
    summary = ("Cites " + ", ".join(parts) + ".") if parts else "Nothing in the text can be checked after the fact."
    return Finding("milestones", "", min(95, score), summary, claims)


# ---------------------------------------------------------------- treasury
def treasury(ctx: ReviewContext, kb: dict[str, Any] | None = None, *, window_days: int = 90) -> Finding:
    t = ctx.target
    amount = int(t["amount"])
    approved = ctx.same_token([p for p in ctx.decided() if p["status"] == "Approved"])
    recent = [p for p in approved if p["submission_time"] >= ctx.cutoff_ns - window_days * DAY_NS]
    spend = sum(int(p["amount"]) for p in recent)
    claims = [
        Claim(
            text=f"Approved outflow in this token over the prior {window_days} days: {fmt_amount(spend, ctx.decimals, t['token_id'])} in {len(recent)} transfers.",
            kind="sum_amount",
            value=str(spend),
            sources=_ids(ctx.dao, recent),
            params={"token": t["token_id"], "window_days": window_days},
        )
    ]
    if spend == 0:
        return Finding("treasury", "", 45, f"No approved outflow in the prior {window_days} days to size this against.", claims)
    share = amount / (spend + amount)
    if share < 0.05:
        score = 85
    elif share < 0.15:
        score = 72
    elif share < 0.30:
        score = 58
    elif share < 0.50:
        score = 45
    else:
        score = 30
    return Finding(
        "treasury",
        "",
        score,
        f"Would be {share:.0%} of the trailing {window_days}-day outflow plus this request.",
        claims,
    )


# ---------------------------------------------------------------- risk
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def risk(ctx: ReviewContext, kb: dict[str, Any] | None = None) -> Finding:
    t = ctx.target
    desc = t["description"]
    proposer, receiver = _parties(ctx)
    tid = ctx.target_id
    claims: list[Claim] = []
    penalty = 0
    earlier = list(ctx.history)
    dupes = [p for p in earlier if p["proposer"] == proposer and _norm(p["description"]) == _norm(desc) and desc.strip()]
    if dupes:
        claims.append(
            Claim(
                f"Same text as {len(dupes)} earlier proposal{'s' if len(dupes) > 1 else ''} from this proposer.",
                "duplicate",
                len(dupes),
                _ids(ctx.dao, dupes),
            )
        )
        penalty += 25
    repeats = [
        p
        for p in earlier
        if p["proposer"] == proposer
        and p["receiver_id"] == receiver
        and p["token_id"] == t["token_id"]
        and p["amount"] == t["amount"]
        and p["submission_time"] >= ctx.cutoff_ns - 30 * DAY_NS
        and p not in dupes
    ]
    if repeats:
        claims.append(
            Claim(
                f"Same proposer, receiver and amount as {len(repeats)} request{'s' if len(repeats) > 1 else ''} filed in the previous 30 days.",
                "repeat_request",
                len(repeats),
                _ids(ctx.dao, repeats),
            )
        )
        penalty += 25
    burst = [p for p in earlier if p["proposer"] == proposer and p["submission_time"] >= ctx.cutoff_ns - DAY_NS]
    if len(burst) >= 3:
        claims.append(
            Claim(
                f"Proposer filed {len(burst)} other transfer requests in the 24 h before this one.",
                "burst",
                len(burst),
                _ids(ctx.dao, burst),
            )
        )
        penalty += 15
    text_len = len(URL_RE.sub("", desc).replace("$$$$", "").strip())
    if text_len < 40:
        claims.append(
            Claim(
                f"Description has only {text_len} characters of text.",
                "target_field",
                text_len,
                [tid],
                {"field": "text_length"},
            )
        )
        penalty += 15
    if not URL_RE.search(desc):
        claims.append(Claim("No supporting link.", "absent", "url", [tid], {"pattern": "url"}))
        penalty += 10
    if IMPLICIT_RE.match(receiver):
        claims.append(
            Claim("Receiver is an implicit (hex) account with no name.", "target_field", True, [tid], {"field": "implicit_receiver"})
        )
        penalty += 10
    prior_any = [p for p in earlier if p["proposer"] == proposer]
    claims.append(
        Claim(
            f"Proposer filed {len(prior_any)} earlier transfer request{'' if len(prior_any) == 1 else 's'} in this DAO.",
            "count",
            len(prior_any),
            _ids(ctx.dao, prior_any),
            {"proposer": proposer, "receiver": None, "status": None},
        )
    )
    if not prior_any:
        penalty += 10
    if receiver != proposer:
        claims.append(
            Claim(f"Funds go to {receiver}, not to the proposer.", "target_field", receiver, [tid], {"field": "receiver_id"})
        )
        penalty += 0  # informational: payouts to a team multisig are normal
    score = max(10, 90 - penalty)
    flags = [
        c for c in claims
        if not (c.kind == "target_field" and c.params.get("field") == "receiver_id") and not (c.kind == "count" and c.value)
    ]
    summary = "No red flags." if not flags else f"{len(flags)} risk signal{'s' if len(flags) > 1 else ''}."
    return Finding("risk", "", score, summary, claims)


ANALYSES = {
    "history": history,
    "budget": budget,
    "milestones": milestones,
    "treasury": treasury,
    "risk": risk,
}

__all__ = ["ANALYSES", "DECIDED", "fmt_amount"]
