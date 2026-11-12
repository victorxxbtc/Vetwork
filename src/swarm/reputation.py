"""Recompute agent reputation from history, with no trust in Vetwork's own numbers.

Input is the contract's EVENT_JSON log lines (from any NEAR indexer or explorer, or
the sandbox's `docs/sandbox-events.jsonl`), or the engine's demo events. Output
matches the contract's `get_reputation` view exactly:

    score = (accepted + 1) / (accepted + rejected + 3 * slashed + 2)

accepted  = sections the reviewer paid (`payout`)
rejected  = sections sent back to the runner-up (`section_rejected`)
slashed   = upheld challenges: paid work later shown to contradict its source (`slashed`)

`swarm reputation events.jsonl` prints the table.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Rep:
    accepted: int = 0
    rejected: int = 0
    slashed: int = 0

    @property
    def score_bp(self) -> int:
        # integer division, as the contract does
        return (self.accepted + 1) * 10_000 // (self.accepted + self.rejected + 3 * self.slashed + 2)

    def to_dict(self) -> dict[str, int]:
        return {"accepted": self.accepted, "rejected": self.rejected, "slashed": self.slashed, "score_bp": self.score_bp}


def parse_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    if "EVENT_JSON:" in line:
        line = line.split("EVENT_JSON:", 1)[1]
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def recompute(events: Iterable[dict[str, Any]]) -> dict[str, Rep]:
    reps: dict[str, Rep] = defaultdict(Rep)
    for e in events:
        kind = e.get("event") or e.get("type")
        agent = e.get("agent")
        if not agent:
            continue
        if kind == "payout":
            reps[agent].accepted += 1
        elif kind == "section_rejected":
            reps[agent].rejected += 1
        elif kind == "slashed":
            reps[agent].slashed += 1
        elif kind == "reviewed" and "accepted" in e and not e["accepted"]:
            reps[agent].rejected += 1  # engine (demo) events: a payout event follows an accepted review
    return dict(reps)


def from_file(path: Path) -> dict[str, Rep]:
    return recompute(e for e in (parse_line(l) for l in path.read_text().splitlines()) if e)
