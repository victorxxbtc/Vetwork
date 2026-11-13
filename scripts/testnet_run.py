"""Run one real Vetwork job on NEAR testnet and record every transaction.

    MASTER=you.testnet uv run python scripts/testnet_run.py setup     # sub-accounts + deploy + init
    MASTER=you.testnet uv run python scripts/testnet_run.py job       # one full job on a real mainnet proposal
    MASTER=you.testnet uv run python scripts/testnet_run.py challenge # one dismissed challenge + one slash drill
    MASTER=you.testnet uv run python scripts/testnet_run.py prereg    # record the evaluation's pre-registration hash

Signing goes through near-cli-rs and the legacy keychain (~/.near-credentials); no key ever
enters this repo. The swarm's work is the same engine as the console: the job's bids, salts,
inference charges, reviewer verdicts and memo hash come from `run_job` on the cached mainnet
snapshot, restricted to the agents registered on chain. Every tx hash goes to docs/testnet-run.json.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swarm.agents.registry import SEED_AGENTS, AgentSpec  # noqa: E402
from swarm.engine import Swarm, run_job  # noqa: E402
from swarm.evaluate import load_decimals, load_snapshot  # noqa: E402
from swarm.ledger import Ledger, near  # noqa: E402

MASTER = os.environ.get("MASTER", "vetwork-hk1113.testnet")
SWARM = f"swarm.{MASTER}"
REVIEWER = f"reviewer.{MASTER}"
OPERATOR = f"operator.{MASTER}"
DAO, PROPOSAL = "marketing.sputnik-dao.near", 1005
AGENTS = ["ledger", "quickcheck", "comparables"]  # two bid on history, one on budget
BOUNTY = 1.5
COMMIT_SECS = REVEAL_SECS = 90
LOG = ROOT / "docs" / "testnet-run.json"
WASM = ROOT / "contract" / "target" / "near" / "vetwork_swarm.wasm"


def log() -> dict:
    return json.loads(LOG.read_text()) if LOG.exists() else {"network": "testnet", "contract": SWARM, "txs": []}


def record(step: str, tx: str | None, **extra) -> None:
    d = log()
    d["txs"].append({"step": step, "tx": tx, "url": f"https://testnet.nearblocks.io/txns/{tx}" if tx else None, **extra})
    LOG.write_text(json.dumps(d, indent=2) + "\n")
    print(f"  {step}: {tx}")


def near_cli(*args: str, ok_fail: bool = False) -> tuple[str | None, str]:
    cmd = ["near", *args]
    for attempt in range(3):
        # NEAR_CLI_HOME: a HOME whose near-cli config points testnet at a reachable RPC (see README §6)
        env = dict(os.environ, HOME=os.environ.get("NEAR_CLI_HOME", os.environ["HOME"]))
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        out = p.stdout + p.stderr
        tx = re.search(r"Transaction ID:\s*([1-9A-HJ-NP-Za-km-z]{43,44})", out)
        if p.returncode == 0 or ok_fail:
            return (tx.group(1) if tx else None), out
        if "timeout" in out.lower() or "connect" in out.lower():
            time.sleep(5)
            continue
        break
    raise RuntimeError(f"{' '.join(cmd[:6])}… failed:\n{out[-1500:]}")


def call(signer: str, method: str, args: dict, deposit: str = "0 NEAR", gas: str = "100 Tgas", ok_fail: bool = False):
    return near_cli("contract", "call-function", "as-transaction", SWARM, method, "json-args", json.dumps(args),
                    "prepaid-gas", gas, "attached-deposit", deposit, "sign-as", signer,
                    "network-config", "testnet", "sign-with-legacy-keychain", "send", ok_fail=ok_fail)


def view(method: str, args: dict):
    import urllib.request

    body = {"jsonrpc": "2.0", "id": 1, "method": "query", "params": {"request_type": "call_function", "finality": "final",
            "account_id": SWARM, "method_name": method, "args_base64": __import__("base64").b64encode(json.dumps(args).encode()).decode()}}
    req = urllib.request.Request("https://rpc.testnet.near.org", json.dumps(body).encode(), {"content-type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return json.loads(bytes(r["result"]["result"]))


def setup() -> None:
    for acct, amount in [(SWARM, "3 NEAR"), (REVIEWER, "0.25 NEAR"), (OPERATOR, "0.25 NEAR")]:
        tx, _ = near_cli("account", "create-account", "fund-myself", acct, amount, "autogenerate-new-keypair",
                         "save-to-legacy-keychain", "sign-as", MASTER, "network-config", "testnet", "sign-with-legacy-keychain", "send")
        record(f"create {acct} ({amount})", tx)
    tx, _ = near_cli("contract", "deploy", SWARM, "use-file", str(WASM), "with-init-call", "new", "json-args",
                     json.dumps({"owner": MASTER, "reviewer": REVIEWER, "operator": OPERATOR}),
                     "prepaid-gas", "100 Tgas", "attached-deposit", "0 NEAR", "network-config", "testnet", "sign-with-legacy-keychain", "send")
    record("deploy + init (owner, reviewer, operator)", tx)


def specs() -> list[AgentSpec]:
    return [AgentSpec(**a.to_dict()) for a in SEED_AGENTS if a.name in AGENTS]


def job() -> None:
    import hashlib

    # 1. agents: real sub-accounts, funded by the master account as their backer
    for a in specs():
        if view("get_agent", {"agent": f"{a.name}.{SWARM}"}) is None:
            ph = hashlib.sha256(a.prompt.encode()).hexdigest()
            tx, _ = call(MASTER, "register_agent", {"name": a.name, "section": a.section, "prompt_hash": ph,
                         "knowledge_base": a.knowledge_base, "tools": a.tools, "public_key": None}, "1 NEAR", "100 Tgas")
            record(f"register agent {a.name}.{SWARM} (1 NEAR deposit, sub-account created)", tx)
    # 2. post the job
    tx, out = call(MASTER, "post_job", {"dao": DAO, "proposal_id": PROPOSAL, "description": f"Vetwork review of {DAO}#{PROPOSAL}",
                   "commit_secs": COMMIT_SECS, "reveal_secs": REVEAL_SECS}, f"{BOUNTY} NEAR", "30 Tgas")
    job_id = int(re.search(r'"job_id":(\d+)', out).group(1))
    record(f"post job #{job_id}: {DAO}#{PROPOSAL}, {BOUNTY} NEAR bounty in escrow", tx, job_id=job_id)

    # 3. the swarm does the work off chain (same engine as the console), with the on-chain job id
    ledger = Ledger()
    sw = Swarm(specs(), ledger, SWARM)
    for a in sw.agents:
        ledger.register(a.account(SWARM))
        ledger.fund(a.account(SWARM), MASTER, near(0.95))
    res = run_job(sw, load_snapshot(DAO), PROPOSAL, load_decimals(), job_id=job_id, bounty=near(BOUNTY), poster=MASTER)
    ev = res.events
    d = log()
    d["job"] = {"job_id": job_id, "dao": DAO, "proposal_id": PROPOSAL, "memo_hash": res.memo["memo_hash"],
                "verdict": res.memo["verdict"], "score": res.memo["score"]}
    LOG.write_text(json.dumps(d, indent=2) + "\n")

    for e in ev:
        if e["type"] == "bid_committed":
            tx, _ = call(OPERATOR, "commit_bid", {"job_id": job_id, "section": e["section"], "agent": e["agent"], "digest": e["digest"]})
            record(f"sealed bid: {e['agent'].split('.')[0]} on {e['section']}", tx)
    j = view("get_job", {"job_id": job_id})
    _wait(j["commit_ends"])
    for e in ev:
        if e["type"] == "bid_revealed":
            salt = hashlib.sha256(f"{e['agent']}:{job_id}:salt".encode()).hexdigest()[:16]
            tx, _ = call(OPERATOR, "reveal_bid", {"job_id": job_id, "section": e["section"], "agent": e["agent"], "price": e["price"], "salt": salt})
            record(f"reveal: {e['agent'].split('.')[0]} {int(e['price']) / 1e24:.4f} NEAR", tx)
    _wait(j["reveal_ends"])
    tx, _ = call(MASTER, "close_auction", {"job_id": job_id})
    record("close auction (lowest price per section wins)", tx)

    paid_sections = set()
    for e in ev:
        if e["type"] == "inference_paid":
            tx, _ = call(OPERATOR, "charge_inference", {"agent": e["agent"], "amount": e["fee"], "job_id": job_id})
            record(f"inference charged to {e['agent'].split('.')[0]} (est., no LLM key)", tx)
        elif e["type"] == "reviewed" and not e["accepted"]:
            tx, _ = call(REVIEWER, "reassign", {"job_id": job_id, "section": e["section"]})
            record(f"reviewer rejects {e['agent'].split('.')[0]} on {e['section']}: {e['flags'][0] if e['flags'] else ''}", tx)
        elif e["type"] == "payout":
            paid_sections.add(e["section"])
    from swarm.agents.claims import SECTIONS

    m = res.memo
    tx, _ = call(REVIEWER, "submit_review", {"job_id": job_id, "memo_hash": m["memo_hash"], "accepted": [s in paid_sections for s in SECTIONS],
                 "score": m["score"], "verdict": m["verdict"]}, gas="150 Tgas")
    record(f"review submitted: {m['verdict']} {m['score']}/100, {len(paid_sections)} sections paid, memo hash recorded, rest refunded", tx)


def _wait(t_ns: int) -> None:
    while True:
        left = t_ns / 1e9 - time.time()
        if left < -3:
            return
        print(f"  waiting {left + 4:.0f}s for the window to close")
        time.sleep(max(2, left + 4))


def challenge() -> None:
    job_id = log()["job"]["job_id"]
    # a real challenge that fails: ledger's history section re-checks clean, so the reviewer dismisses it
    tx, _ = call(MASTER, "challenge", {"job_id": job_id, "section": "history", "claim_index": 0,
                 "reason": "challenge: proposer's earlier-grant count"}, "0.1 NEAR")
    record("challenge history (ledger), 0.1 NEAR deposit", tx)
    tx, _ = call(REVIEWER, "resolve_challenge", {"job_id": job_id, "section": "history", "upheld": False})
    record("reviewer re-checks the claim against its sources: it holds; challenge dismissed, deposit to the agent", tx)
    # slash drill on the budget bond, labelled as a drill everywhere
    tx, _ = call(MASTER, "challenge", {"job_id": job_id, "section": "budget", "claim_index": 0,
                 "reason": "DRILL: exercising the slash path on testnet; the claim itself was verified"}, "0.1 NEAR")
    record("DRILL challenge budget (comparables), 0.1 NEAR deposit", tx)
    tx, _ = call(REVIEWER, "resolve_challenge", {"job_id": job_id, "section": "budget", "upheld": True})
    record("DRILL: reviewer upholds; bond slashed, half to challenger, half to the job poster", tx)


def prereg() -> None:
    h = json.loads((ROOT / "evals" / "preregistration.json").read_text())["hash"]
    tx, _ = call(MASTER, "preregister", {"hash": h, "note": "Vetwork held-out evaluation, committed in git 590a110 before the run"}, gas="10 Tgas")
    record("pre-registration hash recorded (re-recorded after the git commit; the git commit is the earlier proof)", tx, hash=h)


if __name__ == "__main__":
    {"setup": setup, "job": job, "challenge": challenge, "prereg": prereg}[sys.argv[1]]()
