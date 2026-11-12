"""Reputation recomputed in Python from real sandbox chain events equals the contract's own view."""

import json
from pathlib import Path

from swarm.reputation import Rep, from_file, recompute

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_python_recompute_matches_contract_view():
    # both files are written by contract/tests/reputation.rs against a real sandbox chain
    reps = from_file(DOCS / "sandbox-events.jsonl")
    views = json.loads((DOCS / "sandbox-reputation.json").read_text())
    assert views, "no on-chain views recorded"
    for agent, v in views.items():
        assert reps[agent].to_dict() == {k: v[k] for k in ("accepted", "rejected", "slashed", "score_bp")}


def test_slashing_costs_more_than_rejection():
    assert Rep(3, 0, 1).score_bp < Rep(3, 1, 0).score_bp < Rep(3, 0, 0).score_bp


def test_engine_events_count_rejections():
    ev = [{"type": "reviewed", "agent": "a", "accepted": False}, {"type": "payout", "agent": "b"}]
    r = recompute(ev)
    assert (r["a"].rejected, r["b"].accepted) == (1, 1)
