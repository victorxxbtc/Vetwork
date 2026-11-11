"""One review job, end to end: post → sealed bids → work → review → pay → record.

Every step appends an event; the web console replays those events over SSE
and the eval reads the final memo. Given the same inputs the run is fully
deterministic (salts and bid jitter come from hashes, not randomness).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .agents.analysts import ANALYSES, fmt_amount
from .agents.claims import SECTIONS, Claim, Finding
from .agents.llm import NearAI, cost_usd, estimate_tokens, parse_quotes
from .agents.registry import SEED_AGENTS, SEED_BACKING, AgentSpec
from .bidding import SealedAuction, commitment
from .context import ReviewContext, build_context
from .ledger import YOCTO, Ledger, LedgerError, near
from .review.memo import build_memo
from .review.blind import blind_review, llm_support_check

SWARM_ACCOUNT = os.environ.get("SWARM_ACCOUNT", "vetwork.testnet")
NEAR_USD = float(os.environ.get("NEAR_USD", "3.00"))  # assumed rate, only used to convert inference USD to NEAR
DEFAULT_BOUNTY = near(2.0)
REVIEWER_MODEL = os.environ.get("REVIEWER_MODEL", "Qwen/Qwen3.8-27B")


def _unit(*parts: Any) -> float:
    h = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


@dataclass
class Swarm:
    agents: list[AgentSpec]
    ledger: Ledger
    account: str = SWARM_ACCOUNT

    @classmethod
    def seeded(cls, account: str = SWARM_ACCOUNT) -> "Swarm":
        ledger = Ledger()
        agents = [AgentSpec(**a.to_dict()) for a in SEED_AGENTS]
        for a in agents:
            ledger.register(a.account(account))
        for name, backer, amt in SEED_BACKING:
            ledger.fund(f"{name}.{account}", backer, near(amt))
        return cls(agents, ledger, account)

    def add_agent(self, spec: AgentSpec, backer: str, deposit: int) -> None:
        acct = spec.account(self.account)
        self.ledger.register(acct)
        self.ledger.fund(acct, backer, deposit)
        self.agents.append(spec)

    def by_account(self, acct: str) -> AgentSpec:
        return next(a for a in self.agents if a.account(self.account) == acct)


@dataclass
class JobResult:
    memo: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    inference_usd: float = 0.0
    backend: str = "scripted"


def _bid_price(spec: AgentSpec, job_id: int, cap: int) -> int:
    jitter = 0.85 + 0.30 * _unit(spec.name, job_id, "price")
    price = near(spec.base_price_near * jitter)
    return min(price, cap)


def _run_agent(spec: AgentSpec, ctx: ReviewContext, kb: dict[str, Any], llm: NearAI | None) -> Finding:
    fn = ANALYSES[spec.section]
    kwargs: dict[str, Any] = {}
    if spec.section == "history" and spec.params.get("look_ahead"):
        kwargs["look_ahead"] = True
    if spec.section in ("budget", "treasury") and "window_days" in spec.params:
        kwargs["window_days"] = int(spec.params["window_days"])
    f = fn(ctx, kb, **kwargs)
    f.agent = spec.name
    f.model = spec.model
    tool_text = json.dumps([c.text for c in f.claims])
    user = f"Proposal:\n{ctx.target['description'][:2500]}\n\nTool output:\n{tool_text}"
    if llm is None:
        # demo mode: no call is made; we meter what the call would cost.
        f.tokens_in = estimate_tokens(spec.prompt + user)
        f.tokens_out = 90
        return f
    if spec.requires_llm and spec.section == "milestones":
        r = llm.chat(
            spec.model,
            spec.prompt + ' Reply with a JSON list of objects {"quote": "..."} copied verbatim from the proposal.',
            ctx.target["description"][:4000],
            max_tokens=400,
        )
        f.tokens_in, f.tokens_out = r.tokens_in, r.tokens_out
        quotes = parse_quotes(r.text)
        f.claims = [Claim(f"Milestone: “{q}”", "quote", q, [ctx.target_id], {"quote": q}) for q in quotes] or [
            Claim("No checkable milestone found.", "absent", "url", [ctx.target_id], {"pattern": "url"})
        ]
        f.score = min(95, 30 + 15 * len(quotes))
        f.summary = f"{len(quotes)} checkable milestones quoted." if quotes else "No checkable milestones."
        return f
    r = llm.chat(spec.model, spec.prompt + " Write one plain sentence for a DAO council. No numbers that are not in the tool output.", user, 160)
    f.tokens_in, f.tokens_out = r.tokens_in, r.tokens_out
    if r.text:
        f.summary = r.text.splitlines()[0][:300]
    return f


def run_job(
    swarm: Swarm,
    snapshot: dict[str, Any],
    proposal_id: int,
    decimals: dict[str, int],
    *,
    job_id: int,
    bounty: int = DEFAULT_BOUNTY,
    llm: NearAI | None = None,
    poster: str | None = None,
) -> JobResult:
    ev: list[dict[str, Any]] = []
    dao = snapshot["dao"]
    poster = poster or dao
    ctx = build_context(snapshot, proposal_id, decimals)
    kb = {"export": snapshot}
    t = ctx.target
    amount = fmt_amount(t["amount"], ctx.decimals, t["token_id"])
    ledger = swarm.ledger
    ledger.post_job(job_id, bounty)
    ev.append({"type": "job_posted", "job_id": job_id, "dao": dao, "proposal_id": proposal_id, "poster": poster,
               "bounty": str(bounty), "amount": amount, "sections": list(SECTIONS)})

    cap = bounty // len(SECTIONS)
    auction = SealedAuction(job_id, list(SECTIONS), cap)
    salts: dict[tuple[str, str], tuple[int, str]] = {}
    # minimum balance to take part: enough to pay for one call at the agent's model
    for spec in swarm.agents:
        acct = spec.account(swarm.account)
        if spec.requires_llm and llm is None:
            continue
        reserve = near(0.01)
        if ledger.agents[acct].balance < reserve:
            ev.append({"type": "bid_skipped", "agent": acct, "section": spec.section, "reason": "balance below inference reserve"})
            continue
        price = _bid_price(spec, job_id, cap)
        salt = hashlib.sha256(f"{acct}:{job_id}:salt".encode()).hexdigest()[:16]
        digest = commitment(acct, job_id, spec.section, price, salt)
        auction.commit(acct, spec.section, digest)
        salts[(acct, spec.section)] = (price, salt)
        ev.append({"type": "bid_committed", "agent": acct, "section": spec.section, "digest": digest})
    auction.start_reveal()
    for (acct, section), (price, salt) in salts.items():
        auction.reveal(acct, section, price, salt)
        ev.append({"type": "bid_revealed", "agent": acct, "section": section, "price": str(price)})
    auction.close()

    accepted: dict[str, Finding] = {}
    rejected: list[Finding] = []
    prices: dict[str, int] = {}
    inference_usd = 0.0
    for section in SECTIONS:
        ranked = sorted(
            (c for c in auction.commits.values() if c.section == section and c.revealed_price is not None),
            key=lambda c: (c.revealed_price, c.seq),
        )
        for rank, c in enumerate(ranked):
            spec = swarm.by_account(c.agent)
            ev.append({"type": "awarded" if rank == 0 else "reassigned", "agent": c.agent, "section": section,
                       "price": str(c.revealed_price)})
            f = _run_agent(spec, ctx, kb, llm)
            usd = cost_usd(spec.model, f.tokens_in, f.tokens_out)
            fee = max(1, int(usd / NEAR_USD * YOCTO))
            try:
                ledger.charge_inference(c.agent, fee)
            except LedgerError:
                ev.append({"type": "bid_skipped", "agent": c.agent, "section": section, "reason": "could not pay inference"})
                continue
            inference_usd += usd
            ev.append({"type": "inference_paid", "agent": c.agent, "section": section, "model": spec.model,
                       "tokens_in": f.tokens_in, "tokens_out": f.tokens_out, "usd": round(usd, 6), "fee": str(fee),
                       "metered": llm is not None})
            verdict = blind_review(f, ctx)
            support = None
            if llm is not None and verdict.accepted and f.summary:
                support = llm_support_check(llm, REVIEWER_MODEL, f.summary, [c2.text for c2 in f.claims])
            ev.append({"type": "reviewed", "agent": c.agent, "section": section, "accepted": verdict.accepted,
                       "verified": verdict.verified, "flagged": verdict.flagged, "score": f.score,
                       "blind": True, "orders_agree": verdict.orders_agree, "llm_support": support,
                       "flags": [c2.reason for c2 in f.claims if c2.status == "flagged"][:3]})
            if verdict.accepted:
                accepted[section] = f
                prices[section] = c.revealed_price or 0
                ledger.pay(job_id, c.agent, prices[section])
                ev.append({"type": "payout", "agent": c.agent, "section": section, "amount": str(prices[section])})
                break
            rejected.append(f)

    memo = build_memo(dao=dao, proposal=t, amount_display=amount, accepted=accepted, rejected=rejected,
                      prices=prices, inference_usd=inference_usd, job_id=job_id)
    refund = ledger.refund(job_id)
    ev.append({"type": "refund", "to": poster, "amount": str(refund)})
    ev.append({"type": "memo_recorded", "dao": dao, "proposal_id": proposal_id, "memo_hash": memo["memo_hash"],
               "score": memo["score"], "verdict": memo["verdict"]})
    return JobResult(memo, ev, inference_usd, "nearai" if llm else "scripted")
