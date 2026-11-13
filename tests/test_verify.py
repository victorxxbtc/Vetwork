"""`swarm verify` against a fake chain: passes when the chain matches a local re-run, fails when it doesn't."""

import json

from swarm.agents.claims import SECTIONS
from swarm.chain.rpc import RpcError
from swarm.chain.verify import recompute_memo, verify

C, JOB = "swarm.vetwork-hk1113.testnet", 2
B = 1_500_000_000_000_000_000_000_000


def chain(memo_hash=None):
    job = {"poster": "vetwork-hk1113.testnet", "dao": "marketing.sputnik-dao.near", "proposal_id": 1005, "bounty": str(B),
           "escrow": "0", "status": "Reviewed", "memo_hash": None,
           "sections": [{"name": s, "bidders": [], "ranked": [], "winner_idx": 0} for s in SECTIONS]}
    h, b = job["sections"][0], job["sections"][1]
    h["bidders"] = [f"ledger.{C}", f"quickcheck.{C}"]
    h["ranked"] = [[f"quickcheck.{C}", "140000000000000000000000"], [f"ledger.{C}", "270000000000000000000000"]]
    h["winner_idx"] = 1
    b["bidders"] = [f"comparables.{C}"]
    b["ranked"] = [[f"comparables.{C}", "230000000000000000000000"]]
    m = recompute_memo(job, C, JOB, job["poster"])
    job["memo_hash"] = memo_hash or m["memo_hash"]
    agent = {"earned": "270000000000000000000000", "slashed": "0"}
    rep = {"accepted": 1, "rejected": 0, "slashed": 0, "score_bp": 6666}

    class Fake:
        def view(self, account, method, args=None):
            return {"get_job": job, "get_memo": {"memo_hash": job["memo_hash"], "verdict": m["verdict"], "score": m["score"]},
                    "get_bond": {"agent": f"ledger.{C}" if args.get("section") == "history" else f"comparables.{C}",
                                 "amount": "1", "until": 0, "settled": False},
                    "get_agent": agent, "get_reputation": rep}[method]

        def view_account(self, a):
            if "nobody" in a:
                raise RpcError("no")
            return {}
    return Fake(), m


def run_file(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(json.dumps({"contract": C, "job": {"job_id": JOB}}))
    return p


def test_verify_passes_when_chain_matches_local_rerun(tmp_path):
    rpc, m = chain()
    checks = verify(run_file(tmp_path), rpc)
    assert all(ok for ok, _ in checks), [c for c in checks if not c[0]]
    assert any("matches the chain" in msg for _, msg in checks)
    assert any("rejected quickcheck" in msg for _, msg in checks)


def test_verify_fails_on_a_tampered_memo_hash(tmp_path):
    rpc, _ = chain(memo_hash="f" * 64)
    checks = verify(run_file(tmp_path), rpc)
    assert any(not ok and "DOES NOT match" in msg for ok, msg in checks)
