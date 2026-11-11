"""Blind, two-order review.

The reviewer never sees who wrote a section, what they bid, who backs them,
their model, their score or their prose: only the claims and the record they
cite. Each section is reviewed twice, with the claims in the order the agent
gave them and in reverse. A claim's verdict must not depend on its position;
if the two passes disagree on any claim the section is not accepted.

The rule-based checker is order-independent by construction, so for it the
two passes agree on every section; the check is there so that any reviewer
put in its place (an LLM judge in NEAR AI mode, for example) is held to the
same rule, and the agreement rate is reported by the evaluation either way.
The optional LLM pass (`llm_support_check`) asks whether the agent's prose is
backed by its verified claims, shown both ways round; disagreement → "unsure".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from ..agents.claims import Claim, Finding
from ..context import ReviewContext
from .reviewer import SectionVerdict, check_claim


@dataclass
class BlindVerdict(SectionVerdict):
    orders_agree: bool = True


def anonymise(f: Finding) -> list[Claim]:
    """What the reviewer is shown: claim kind, value, params and citations. Nothing else."""
    return [Claim(text="", kind=c.kind, value=copy.deepcopy(c.value), sources=list(c.sources), params=copy.deepcopy(c.params)) for c in f.claims]


def _pass(claims: list[Claim], ctx: ReviewContext, order: list[int]) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for i in order:
        c = check_claim(copy.deepcopy(claims[i]), ctx)
        out[i] = (c.status, c.reason)
    return out


def blind_review(f: Finding, ctx: ReviewContext) -> BlindVerdict:
    shown = anonymise(f)
    idx = list(range(len(shown)))
    fwd = _pass(shown, ctx, idx)
    rev = _pass(shown, ctx, idx[::-1])
    agree = fwd == rev
    for i, c in enumerate(f.claims):  # write the verdicts back onto the agent's own claims
        c.status, c.reason = fwd[i]
        if not agree and rev[i] != fwd[i]:
            c.status, c.reason = "flagged", "verdict changed with claim order"
    flagged = sum(c.status == "flagged" for c in f.claims)
    verified = sum(c.status == "verified" for c in f.claims)
    return BlindVerdict(
        f.section, f.agent, accepted=bool(f.claims) and flagged == 0 and agree,
        verified=verified, flagged=flagged, orders_agree=agree,
    )


SUPPORT_PROMPT = (
    "You check a grant-review note. Below are EVIDENCE lines (verified facts) and a NOTE. "
    "Answer with one word: SUPPORTED if every factual statement in the NOTE appears in the EVIDENCE, "
    "otherwise UNSUPPORTED. You are not told who wrote the note."
)


def llm_support_check(llm, model: str, summary: str, evidence: list[str]) -> dict:
    """Ask twice, blind, with NOTE first and with EVIDENCE first. Agreement is reported."""
    ev = "\n".join(f"EVIDENCE: {e}" for e in evidence)
    a = llm.chat(model, SUPPORT_PROMPT, f"NOTE: {summary}\n{ev}", 8)
    b = llm.chat(model, SUPPORT_PROMPT, f"{ev}\nNOTE: {summary}", 8)

    def label(t: str) -> str:
        t = t.strip().upper()
        return "UNSUPPORTED" if "UNSUPPORTED" in t else "SUPPORTED" if "SUPPORTED" in t else "UNSURE"

    la, lb = label(a.text), label(b.text)
    return {
        "first": la, "second": lb, "orders_agree": la == lb,
        "label": la if la == lb else "UNSURE",
        "tokens_in": a.tokens_in + b.tokens_in, "tokens_out": a.tokens_out + b.tokens_out,
    }
