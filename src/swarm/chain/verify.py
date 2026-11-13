"""`swarm verify`: re-read the testnet job live, with no keys, and re-derive its memo hash.

It reads `docs/testnet-run.json` only for the contract id and job id, then:
1. reads the job, memo record, agents, bonds and reputation from testnet RPC (view calls);
2. re-runs the swarm locally on the cached mainnet snapshot with the agents that actually bid
   on chain and the on-chain job id, and checks the memo hash it gets equals the one on chain;
3. checks escrow is empty, accepted sections match the paid agents, and agent sub-accounts exist.
Exit code 0 only if every check passes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .rpc import TESTNET_RPC, NearRpc, RpcError

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "docs" / "testnet-run.json"
YOCTO = 10**24


def recompute_memo(job: dict[str, Any], contract: str, job_id: int, poster: str) -> dict[str, Any]:
    from ..agents.registry import SEED_AGENTS, AgentSpec
    from ..engine import Swarm, run_job
    from ..evaluate import load_decimals, load_snapshot
    from ..ledger import Ledger, near

    names = {b.split(".")[0] for s in job["sections"] for b in s["bidders"]}
    specs = [AgentSpec(**a.to_dict()) for a in SEED_AGENTS if a.name in names]
    ledger = Ledger()
    sw = Swarm(specs, ledger, contract)
    for a in specs:
        ledger.register(a.account(contract))
        ledger.fund(a.account(contract), poster, near(0.95))
    res = run_job(sw, load_snapshot(job["dao"]), job["proposal_id"], load_decimals(), job_id=job_id,
                  bounty=int(job["bounty"]), poster=poster)
    return res.memo


def verify(run_file: Path = RUN, rpc: NearRpc | None = None, recompute: bool = True) -> list[tuple[bool, str]]:
    run = json.loads(run_file.read_text())
    contract, job_id = run["contract"], run["job"]["job_id"]
    rpc = rpc or NearRpc(TESTNET_RPC, timeout=30)
    checks: list[tuple[bool, str]] = []

    def check(ok: bool, msg: str) -> None:
        checks.append((bool(ok), msg))

    job = rpc.view(contract, "get_job", {"job_id": job_id})
    check(job is not None, f"job #{job_id} exists on {contract}")
    if job is None:
        return checks
    check(job["status"] == "Reviewed", f"job status is {job['status']}")
    check(int(job["escrow"]) == 0, "escrow is empty: winners paid, the rest refunded")
    memo = rpc.view(contract, "get_memo", {"dao": job["dao"], "proposal_id": job["proposal_id"]})
    check(memo is not None and memo["memo_hash"] == job["memo_hash"], f"memo record for {job['dao']}#{job['proposal_id']}: {memo and memo['verdict']} {memo and memo['score']}/100, hash {job['memo_hash'][:16]}…")

    for s in job["sections"]:
        if not s["ranked"]:
            continue
        winner = s["ranked"][s["winner_idx"]][0] if s["winner_idx"] < len(s["ranked"]) else None
        bond = rpc.view(contract, "get_bond", {"job_id": job_id, "section": s["name"]})
        if bond:
            check(bond["agent"] == winner, f"{s['name']}: paid {winner.split('.')[0]} {int(bond['amount']) / YOCTO:.2f} NEAR, bonded until {bond['until'] // 10**9}" + (" (settled)" if bond["settled"] else ""))
        if s["winner_idx"] > 0:
            loser = s["ranked"][s["winner_idx"] - 1][0]
            check(True, f"{s['name']}: reviewer rejected {loser.split('.')[0]}, runner-up took over")

    agents = sorted({b for s in job["sections"] for b in s["bidders"]})
    for a in agents:
        info = rpc.view(contract, "get_agent", {"agent": a})
        rep = rpc.view(contract, "get_reputation", {"agent": a})
        try:
            rpc.view_account(a)
            exists = True
        except RpcError:
            exists = False
        check(exists and info is not None, f"{a}: sub-account exists; earned {int(info['earned']) / YOCTO:.2f}, slashed {int(info['slashed']) / YOCTO:.2f}, "
                                           f"reputation {rep['score_bp'] / 100:.1f}% ({rep['accepted']} accepted / {rep['rejected']} rejected / {rep['slashed']} slashed)")

    if recompute:
        m = recompute_memo(job, contract, job_id, job["poster"])
        check(m["memo_hash"] == job["memo_hash"], f"re-ran the swarm locally on the cached mainnet snapshot: memo hash {m['memo_hash'][:16]}… {'matches' if m['memo_hash'] == job['memo_hash'] else 'DOES NOT match'} the chain")
    return checks


def main(run_file: Path = RUN) -> int:
    checks = verify(run_file)
    for ok, msg in checks:
        print(("✓ " if ok else "✗ ") + msg)
    bad = sum(not ok for ok, _ in checks)
    print(f"\n{len(checks) - bad} of {len(checks)} checks passed, read live from testnet (no keys).")
    return 1 if bad else 0
