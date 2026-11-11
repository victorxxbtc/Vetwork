"""`swarm demo | eval | fetch | review`"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .evaluate import RESULTS, pct, run


def _table(o: dict) -> str:
    lines = [
        "| Set | Proposals reviewed | Agreement with the final DAO vote | Always-FUND baseline | Majority baseline | Balanced accuracy | Mean cost per review | Claims flagged |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, m in list(o["per_dao"].items()) + [("all", o["overall"])]:
        lines.append(
            f"| {name} | {m['n']} ({m['approved']} approved / {m['rejected']} rejected) | {pct(m['agreement'])} | "
            f"{pct(m['always_fund_baseline'])} | {pct(m['majority_baseline'])} | {pct(m['balanced_accuracy'])} | "
            f"{m['mean_bids_near']:.2f} NEAR bids + ${m['mean_inference_usd']:.4f} est. inference | "
            f"{m['claims_flagged']:,} of {m['claims']:,} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="swarm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="serve the console on :8000")
    d.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    d.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    e = sub.add_parser("eval", help="run the swarm over decided proposals")
    e.add_argument("--split", default="test", choices=["dev", "test"])
    e.add_argument("--limit", type=int, default=None, help="only the last N decided proposals per DAO")
    f = sub.add_parser("fetch", help="re-download proposals from mainnet RPC (read-only)")
    f.add_argument("daos", nargs="*")
    r = sub.add_parser("review", help="print one memo as JSON")
    r.add_argument("dao")
    r.add_argument("proposal_id", type=int)
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        import uvicorn

        print(f"Vetwork console: http://{a.host}:{a.port}  (mode: {'NEAR AI' if os.environ.get('NEAR_AI_API_KEY') else 'demo, no keys'})")
        uvicorn.run("swarm.web.app:app", host=a.host, port=a.port, log_level="warning")
    elif a.cmd == "eval":
        o = run(a.split, a.limit, write=a.limit is None)
        md = _table(o)
        print(md)
        if a.limit is None:
            (RESULTS / f"{a.split}.md").write_text(md + "\n")
    elif a.cmd == "fetch":
        from pathlib import Path

        from .chain.sputnik import fetch_dao, save
        from .evaluate import DATA, load_config

        cfg = load_config()
        for dao in a.daos or sum(cfg["splits"].values(), []):
            s = fetch_dao(dao)
            print(dao, len(s["proposals"]), "transfer proposals ->", save(s, Path(DATA / "daos")))
    elif a.cmd == "review":
        from .engine import Swarm, run_job
        from .evaluate import load_decimals, load_snapshot

        res = run_job(Swarm.seeded(), load_snapshot(a.dao), a.proposal_id, load_decimals(), job_id=1)
        json.dump(res.memo, sys.stdout, indent=2, ensure_ascii=False)
        print()


if __name__ == "__main__":
    main()
