"""Pre-registration refuses to run when anything pinned has changed."""

import json

import pytest

from swarm import prereg


def test_prereg_roundtrip_and_tamper_detection(tmp_path):
    p = tmp_path / "pr.json"
    doc = prereg.write(["marketing.sputnik-dao.near"], "test", p)
    assert len(doc["hash"]) == 64
    assert prereg.verify(p)["hash"] == doc["hash"]
    # no outcome is listed in the registration: only ids and hashes
    assert "status" not in json.dumps(doc["body"]["snapshots"])

    d = json.loads(p.read_text())
    d["body"]["metric"]["primary_pass"] = "anything goes"
    p.write_text(json.dumps(d))
    with pytest.raises(RuntimeError, match="edited after"):
        prereg.verify(p)

    d["hash"] = prereg.digest(d["body"])  # re-hash the tampered body: still refused, metric differs from code
    p.write_text(json.dumps(d))
    with pytest.raises(RuntimeError, match="changed"):
        prereg.verify(p)


def test_bootstrap_ci_brackets_point_estimate():
    rows = [{"outcome": "Approved", "agree": i % 3 != 0} for i in range(60)] + [
        {"outcome": "Rejected", "agree": i % 2 == 0} for i in range(40)]
    lo, hi = prereg.bootstrap_ci(rows)
    point = (40 / 60 + 20 / 40) / 2
    assert lo <= point <= hi
