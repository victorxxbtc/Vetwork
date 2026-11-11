"""Blind, two-order review: the verdict must not depend on who wrote a claim or where it sits."""

from __future__ import annotations

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from swarm.agents.analysts import ANALYSES
from swarm.agents.claims import Claim, Finding
from swarm.context import build_context
from swarm.evaluate import load_decimals, load_snapshot
from swarm.review.blind import anonymise, blind_review, llm_support_check

DAO = "marketing.sputnik-dao.near"
SNAP = load_snapshot(DAO)
DEC = load_decimals()
CTX = build_context(SNAP, 1005, DEC)


def _finding() -> Finding:
    fs = [ANALYSES[s](CTX, {"export": SNAP}) for s in ("history", "budget", "risk")]
    claims = [c for f in fs for c in f.claims]
    # add one claim that must fail: it cites a proposal that did not exist yet
    claims.append(Claim("bogus", "count", 1, [f"sputnik:{DAO}#99999"], {"proposer": "x"}))
    return Finding("history", "ledger", 80, "summary", claims)


def test_reviewer_is_shown_no_identity():
    shown = anonymise(_finding())
    for c in shown:
        assert c.text == "" and not hasattr(c, "agent")
    assert all(set(vars(c)) == {"text", "kind", "value", "sources", "params", "status", "reason"} for c in shown)


@settings(max_examples=40, deadline=None)
@given(st.permutations(range(8)), st.text(min_size=1, max_size=12))
def test_verdict_is_independent_of_order_and_author(perm, author):
    base = _finding()
    ref = blind_review(copy.deepcopy(base), CTX)
    f = copy.deepcopy(base)
    idx = [i for i in perm if i < len(f.claims)] + list(range(8, len(f.claims)))
    f.claims = [f.claims[i] for i in idx]
    f.agent, f.summary, f.score = author, "anything at all", 3
    v = blind_review(f, CTX)
    assert v.orders_agree and ref.orders_agree
    assert (v.accepted, v.verified, v.flagged) == (ref.accepted, ref.verified, ref.flagged)
    assert not v.accepted  # the bogus claim is caught wherever it sits


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def chat(self, model, system, user, max_tokens):
        self.prompts.append(user)
        from swarm.agents.llm import ChatResult
        return ChatResult(self.replies.pop(0), 10, 1)


def test_llm_support_check_asks_both_orders_and_reports_disagreement():
    llm = FakeLLM(["SUPPORTED", "UNSUPPORTED"])
    r = llm_support_check(llm, "m", "Team delivered 3 grants.", ["3 earlier grants approved"])
    assert llm.prompts[0].startswith("NOTE:") and llm.prompts[1].startswith("EVIDENCE:")
    assert r["orders_agree"] is False and r["label"] == "UNSURE"
    llm = FakeLLM(["SUPPORTED", " supported."])
    assert llm_support_check(llm, "m", "x", ["y"])["label"] == "SUPPORTED"
