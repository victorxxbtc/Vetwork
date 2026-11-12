"""Pre-registration: fix the proposal set, the method and the metric, hash them, run later.

`swarm prereg` writes `evals/preregistration.json` and prints its hash. The file
pins the DAO snapshots by sha256 (so the proposal set cannot change), the
selection rule, the method's source files by sha256 (so the swarm cannot be
tuned after seeing results), and the metric with its pass/fail threshold.
The hash is committed to git before the run and can be recorded on chain with
the contract's `preregister` method. `swarm eval --split prereg` refuses to
run unless every pinned hash still matches.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "swarm"
DATA = ROOT / "evals" / "data"
PREREG = ROOT / "evals" / "preregistration.json"

# the files that decide a verdict; web, MCP and chain-reading code are not part of the method
METHOD_FILES = [
    "context.py", "engine.py", "evaluate.py", "ledger.py",
    "agents/analysts.py", "agents/claims.py", "agents/registry.py", "agents/llm.py",
    "bidding/sealed.py", "review/reviewer.py", "review/blind.py", "review/memo.py",
]

METRIC = {
    "unit": "every Transfer proposal in the pinned snapshots whose final status is Approved or Rejected; all other statuses excluded and counted",
    "prediction": "the swarm's memo verdict; FUND predicts Approved, REVISE or DECLINE predict Rejected",
    "backend": "scripted agents (no LLM calls), deterministic",
    "primary": "balanced accuracy = (recall on Approved + recall on Rejected) / 2, pooled over all pinned DAOs",
    "primary_pass": "balanced accuracy > 0.50 and the 95% stratified bootstrap interval (2,000 resamples within each class, seed 7) excludes 0.50",
    "secondary": ["agreement vs the majority-class baseline", "per-DAO balanced accuracy", "claims flagged / claims made", "reviewer order agreement"],
    "reporting": "all numbers are reported whatever they show; no DAO or proposal is dropped after the run",
}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def body(daos: list[str]) -> dict[str, Any]:
    from .chain.sputnik import load

    snaps = {}
    for d in daos:
        p = DATA / "daos" / f"{d}.json.gz"
        s = load(p)
        # proposal ids only; outcomes are pinned through the snapshot hash, not listed
        snaps[d] = {"sha256": sha256_file(p), "transfer_proposal_ids": [x["id"] for x in s["proposals"]]}
    return {
        "daos": daos,
        "snapshots": snaps,
        "tokens_sha256": sha256_file(DATA / "tokens.json"),
        "method": {f: sha256_file(SRC / f) for f in METHOD_FILES},
        "metric": METRIC,
    }


def digest(b: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(b)).hexdigest()


def write(daos: list[str], note: str, path: Path = PREREG) -> dict[str, Any]:
    b = body(daos)
    doc = {"registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "note": note,
           "hash": digest(b), "body": b}
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def verify(path: Path = PREREG) -> dict[str, Any]:
    """Recompute every pinned hash; raise if anything changed since registration."""
    doc = json.loads(path.read_text())
    b = doc["body"]
    if digest(b) != doc["hash"]:
        raise RuntimeError("preregistration.json was edited after it was hashed")
    now = body(b["daos"])
    diffs = [k for k in ("snapshots", "tokens_sha256", "method", "metric") if now[k] != b[k]]
    if diffs:
        changed = [f for f in b["method"] if now["method"].get(f) != b["method"][f]]
        raise RuntimeError(f"pre-registered inputs changed: {diffs} {changed}")
    return doc


def bootstrap_ci(rows: list[dict[str, Any]], n: int = 2000, seed: int = 7) -> tuple[float, float]:
    import random

    rng = random.Random(seed)
    pos = [r["agree"] for r in rows if r["outcome"] == "Approved"]
    neg = [r["agree"] for r in rows if r["outcome"] == "Rejected"]
    if not pos or not neg:
        return (float("nan"), float("nan"))
    vals = []
    for _ in range(n):
        p = [rng.choice(pos) for _ in pos]
        q = [rng.choice(neg) for _ in neg]
        vals.append((sum(p) / len(p) + sum(q) / len(q)) / 2)
    vals.sort()
    return (vals[int(0.025 * n)], vals[int(0.975 * n) - 1])
