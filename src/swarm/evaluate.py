"""Run the swarm over real, already-decided Sputnik DAO proposals and score it.

Ground truth is the DAO's own final status: `Approved` means funded,
`Rejected` means the council voted it down. `Removed`, `Expired`, `Failed` and
`InProgress` are counted and excluded (no vote outcome to compare against).

The swarm "agrees" when it says FUND on an approved proposal, or REVISE /
DECLINE on a rejected one. We also report the always-FUND baseline, because
most DAO proposals pass and raw agreement alone would flatter any yes-machine.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .chain.sputnik import load
from .engine import Swarm, run_job
from .ledger import YOCTO

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evals" / "data"
RESULTS = ROOT / "evals" / "results"
OUTCOMES = ("Approved", "Rejected")


def load_config() -> dict[str, Any]:
    return json.loads((ROOT / "evals" / "config.json").read_text())


def load_decimals() -> dict[str, int]:
    return json.loads((DATA / "tokens.json").read_text())


def load_snapshot(dao: str) -> dict[str, Any]:
    return load(DATA / "daos" / f"{dao}.json.gz")


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    agree = sum(r["agree"] for r in rows)
    pos = [r for r in rows if r["outcome"] == "Approved"]
    neg = [r for r in rows if r["outcome"] == "Rejected"]
    tpr = sum(r["agree"] for r in pos) / len(pos) if pos else None
    tnr = sum(r["agree"] for r in neg) / len(neg) if neg else None
    bal = (tpr + tnr) / 2 if tpr is not None and tnr is not None else None
    conf = Counter(f"{r['verdict']}|{r['outcome']}" for r in rows)
    return {
        "n": n,
        "approved": len(pos),
        "rejected": len(neg),
        "agreement": agree / n,
        "always_fund_baseline": len(pos) / n,
        "majority_baseline": max(len(pos), len(neg)) / n,
        "recall_approved": tpr,
        "recall_rejected": tnr,
        "balanced_accuracy": bal,
        "confusion": dict(sorted(conf.items())),
        "mean_bids_near": sum(r["bids_yocto"] for r in rows) / n / YOCTO,
        "mean_inference_usd": sum(r["inference_usd"] for r in rows) / n,
        "claims": sum(r["claims"] for r in rows),
        "claims_flagged": sum(r["flagged"] for r in rows),
        "sections_reassigned": sum(r["reassigned"] for r in rows),
        "sections_reviewed": sum(r.get("sections_reviewed", 0) for r in rows),
        "order_agreement": (sum(r.get("orders_agree", 0) for r in rows) / s) if (s := sum(r.get("sections_reviewed", 0) for r in rows)) else None,
    }


def run(split: str = "test", limit: int | None = None, write: bool = True, progress: bool = True) -> dict[str, Any]:
    cfg = load_config()
    decimals = load_decimals()
    prereg = None
    if split == "prereg":
        from .prereg import verify

        prereg = verify()  # raises if the proposal set, method or metric changed since registration
        daos = prereg["body"]["daos"]
    else:
        daos = cfg["splits"][split]
    swarm = Swarm.seeded()
    rows: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    job_id = 0
    t0 = time.time()
    samples: dict[str, Any] = {}
    for dao in daos:
        snap = load_snapshot(dao)
        props = snap["proposals"]
        decided = [p for p in props if p["status"] in OUTCOMES]
        for p in props:
            if p["status"] not in OUTCOMES:
                excluded[p["status"]] += 1
        if limit:
            decided = decided[-limit:]
        for p in decided:
            job_id += 1
            res = run_job(swarm, snap, p["id"], decimals, job_id=job_id)
            m = res.memo
            agree = (m["verdict"] == "FUND") == (p["status"] == "Approved")
            rows.append(
                {
                    "dao": dao,
                    "proposal_id": p["id"],
                    "outcome": p["status"],
                    "verdict": m["verdict"],
                    "score": m["score"],
                    "agree": agree,
                    "bids_yocto": int(m["cost"]["bids_yocto"]),
                    "inference_usd": m["cost"]["inference_usd"],
                    "claims": m["claims_total"],
                    "flagged": m["claims_flagged"],
                    "reassigned": sum(e["type"] == "reassigned" for e in res.events),
                    "sections_reviewed": sum(e["type"] == "reviewed" for e in res.events),
                    "orders_agree": sum(e["type"] == "reviewed" and e.get("orders_agree", False) for e in res.events),
                    "memo_hash": m["memo_hash"],
                }
            )
            key = f"{dao}#{p['id']}"
            if dao in cfg.get("demo_daos", []) and p["id"] in cfg.get("demo_proposals", {}).get(dao, []):
                samples[key] = m
        if progress:
            print(f"  {dao}: {len(decided)} decided proposals reviewed ({time.time() - t0:.0f}s)", flush=True)
    per_dao = {d: _metrics([r for r in rows if r["dao"] == d]) for d in daos}
    out = {
        "split": split,
        "daos": daos,
        "backend": "scripted (deterministic tools; no LLM calls; inference cost is an estimate)",
        "method_frozen_at": cfg["method_frozen_at"],
        "overall": _metrics(rows),
        "per_dao": per_dao,
        "excluded_statuses": dict(excluded),
        "runtime_s": round(time.time() - t0, 1),
    }
    if prereg is not None:
        from .prereg import bootstrap_ci

        lo, hi = bootstrap_ci(rows)
        bal = out["overall"].get("balanced_accuracy")
        out["preregistration"] = {
            "hash": prereg["hash"], "registered_at": prereg["registered_at"],
            "primary": "balanced_accuracy", "value": bal, "ci95": [lo, hi],
            "passed": bool(bal is not None and bal > 0.5 and lo > 0.5),
        }
    if write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"{split}.json").write_text(json.dumps(out, indent=2))
        with (RESULTS / f"{split}-rows.jsonl").open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return out


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"
