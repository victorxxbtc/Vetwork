"""Core swarm behaviour: sealed bids, leakage guard, reviewer, ledger, memo, web."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from swarm.agents.claims import Claim
from swarm.bidding import AuctionError, SealedAuction, commitment
from swarm.context import build_context
from swarm.engine import Swarm, run_job
from swarm.evaluate import load_decimals, load_snapshot
from swarm.ledger import YOCTO, Ledger, LedgerError
from swarm.review.memo import memo_hash, verdict_for
from swarm.review.reviewer import check_claim

DAO = "marketing.sputnik-dao.near"


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(DAO)


@pytest.fixture(scope="module")
def dec():
    return load_decimals()


# ------------------------------------------------------------------ bidding
def test_commitment_matches_contract_vector():
    # the same vector is asserted in contract/src/tests.rs
    assert commitment("ledger.vetwork.testnet", 1, "history", 300_000_000_000_000_000_000_000, "abc") == (
        "cc15a9e8b17dcd3e8aab4ea8987178ec98de3f38926e815772c006165f0e14e1"
    )


bids = st.lists(
    st.tuples(st.integers(1, 10**24), st.text("abcdef0123", min_size=1, max_size=8)), min_size=1, max_size=8
)


@given(bids)
@settings(max_examples=60, deadline=None)
def test_lowest_revealed_bid_wins(entries):
    cap = 10**24
    a = SealedAuction(7, ["history"], cap)
    for i, (price, salt) in enumerate(entries):
        a.commit(f"a{i}", "history", commitment(f"a{i}", 7, "history", price, salt))
    a.start_reveal()
    for i, (price, salt) in enumerate(entries):
        assert a.reveal(f"a{i}", "history", price, salt)
    [r] = a.close()
    best = min(range(len(entries)), key=lambda i: (entries[i][0], i))
    assert r.winner == f"a{best}" and r.price == entries[best][0]


@given(st.integers(1, 10**24), st.integers(1, 10**24))
@settings(max_examples=40, deadline=None)
def test_wrong_price_never_reveals(price, other):
    a = SealedAuction(1, ["risk"], 10**25)
    a.commit("x", "risk", commitment("x", 1, "risk", price, "s"))
    a.start_reveal()
    assert a.reveal("x", "risk", other, "s") is (price == other)


def test_unrevealed_and_over_cap_bids_do_not_win():
    a = SealedAuction(1, ["risk"], 100)
    a.commit("silent", "risk", commitment("silent", 1, "risk", 1, "s"))
    a.commit("greedy", "risk", commitment("greedy", 1, "risk", 500, "s"))
    a.commit("fair", "risk", commitment("fair", 1, "risk", 90, "s"))
    a.start_reveal()
    a.reveal("greedy", "risk", 500, "s")
    a.reveal("fair", "risk", 90, "s")
    assert a.close()[0].winner == "fair"


def test_no_double_commit_or_late_commit():
    a = SealedAuction(1, ["risk"], 100)
    a.commit("x", "risk", "0" * 64)
    with pytest.raises(AuctionError):
        a.commit("x", "risk", "0" * 64)
    a.start_reveal()
    with pytest.raises(AuctionError):
        a.commit("y", "risk", "0" * 64)


# ------------------------------------------------------------------ leakage guard
def test_context_hides_target_outcome_and_future(snap, dec):
    pid = 1005
    ctx = build_context(snap, pid, dec)
    assert "status" not in ctx.target and "vote_counts" not in ctx.target
    assert all(p["submission_time"] < ctx.cutoff_ns for p in ctx.history)
    for p in ctx.history:
        if p["submission_time"] + ctx.period_ns > ctx.cutoff_ns:
            assert "status" not in p  # vote not final yet at submission time


@given(st.integers(min_value=0, max_value=338))
@settings(max_examples=25, deadline=None)
def test_no_context_ever_leaks(i):
    snap = load_snapshot(DAO)
    p = [q for q in snap["proposals"] if q["status"] in ("Approved", "Rejected")][i]
    ctx = build_context(snap, p["id"], load_decimals())
    assert all(h["submission_time"] < ctx.cutoff_ns for h in ctx.history)
    assert "status" not in ctx.target


def test_reviewer_flags_look_ahead_citations(snap, dec):
    ctx = build_context(snap, 1005, dec)
    future = [p for p in snap["proposals"] if p["submission_time"] > ctx.cutoff_ns][0]
    c = Claim("x", "count", 1, [f"sputnik:{DAO}#{future['id']}"], {"proposer": "a", "receiver": "b", "status": ["Approved"]})
    assert check_claim(c, ctx).status == "flagged"
    assert "did not exist" in c.reason


def test_reviewer_flags_invented_quote(snap, dec):
    ctx = build_context(snap, 1005, dec)
    c = Claim("x", "quote", "q", [ctx.target_id], {"quote": "we will onboard 1,000,000 users by Friday"})
    assert check_claim(c, ctx).status == "flagged"


def test_reviewer_flags_wrong_count(snap, dec):
    ctx = build_context(snap, 1005, dec)
    p = ctx.target
    c = Claim("x", "count", 999, [], {"proposer": p["proposer"], "receiver": p["receiver_id"], "status": ["Approved"]})
    assert check_claim(c, ctx).status == "flagged"


# ------------------------------------------------------------------ ledger
@given(st.lists(st.integers(10**20, 10**25), min_size=1, max_size=6), st.integers(0, 10**25))
@settings(max_examples=60, deadline=None)
def test_backers_never_withdraw_more_than_balance(deposits, revenue):
    led = Ledger()
    led.register("a")
    for i, d in enumerate(deposits):
        led.fund("a", f"b{i}", d)
    led.post_job(1, revenue + 1)
    if revenue:
        led.pay(1, "a", revenue)
    total = sum(deposits) + revenue
    out = sum(led.withdraw("a", f"b{i}", led.agents["a"].shares[f"b{i}"]) for i in range(len(deposits)))
    assert out <= total and total - out < len(deposits) + 1  # rounding dust only


def test_inference_cannot_overdraw():
    led = Ledger()
    led.register("a")
    led.fund("a", "b", 10)
    with pytest.raises(LedgerError):
        led.charge_inference("a", 11)


# ------------------------------------------------------------------ end to end
def test_job_is_deterministic_and_pays_from_escrow(snap, dec):
    s1, s2 = Swarm.seeded(), Swarm.seeded()
    r1 = run_job(s1, snap, 1005, dec, job_id=42)
    r2 = run_job(s2, snap, 1005, dec, job_id=42)
    assert r1.memo == r2.memo and r1.memo["memo_hash"] == memo_hash(r1.memo)
    assert r1.memo["verdict"] == verdict_for(r1.memo["score"])
    paid = sum(int(e["amount"]) for e in r1.events if e["type"] == "payout")
    refund = int(next(e for e in r1.events if e["type"] == "refund")["amount"])
    assert paid + refund == 2 * YOCTO
    types = [e["type"] for e in r1.events]
    assert types[0] == "job_posted" and types[-1] == "memo_recorded"
    assert types.index("bid_revealed") > max(i for i, t in enumerate(types) if t == "bid_committed")


def test_naive_agent_is_sent_back_and_not_paid(snap, dec):
    sw = Swarm.seeded()
    r = run_job(sw, snap, 1005, dec, job_id=42)
    rejected = [e for e in r.events if e["type"] == "reviewed" and not e["accepted"]]
    assert any("quickcheck" in e["agent"] for e in rejected)
    paid = {e["agent"] for e in r.events if e["type"] == "payout"}
    assert not any("quickcheck" in a for a in paid)
    assert all(s["accepted"] for s in r.memo["sections"])


def test_added_agent_joins_auction(snap, dec):
    from swarm.agents.registry import AgentSpec

    sw = Swarm.seeded()
    sw.add_agent(AgentSpec("cheap-risk", "Cheap", "risk", "find risks", base_price_near=0.05), "me.testnet", YOCTO)
    r = run_job(copy.deepcopy(sw), snap, 1005, dec, job_id=9)
    assert any(e["type"] == "awarded" and e["agent"].startswith("cheap-risk.") for e in r.events)


# ------------------------------------------------------------------ web
def test_pages_render():
    from fastapi.testclient import TestClient

    from swarm.web.app import app

    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert "FUND" in c.get(f"/memo/{DAO}/1005").text or "REVISE" in c.get(f"/memo/{DAO}/1005").text
    assert c.get("/eval").status_code == 200
    assert c.get("/agents").status_code == 200
    body = c.get("/events?speed=0").text
    assert "memo_recorded" in body and "event: done" in body
    assert c.get(f"/memo/{DAO}/999999").status_code == 404
