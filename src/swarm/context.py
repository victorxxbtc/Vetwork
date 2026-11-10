"""What a reviewer is allowed to know about a proposal at the moment it was submitted.

The point of the evaluation is that the swarm predicts the DAO's vote, so the
context must not leak the future:

* the target proposal is passed without `status` or `vote_counts`;
* an earlier proposal's outcome is visible only if it was submitted at least
  one full voting period (`proposal_period` from the DAO policy) before the
  target, so it was certainly decided by then;
* proposals submitted after the target are not in the context at all.

Every record an agent can cite is a `source` with a stable id
(`sputnik:<dao>#<id>`), so the reviewer can resolve each citation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

DECIDED = {"Approved", "Rejected", "Removed", "Expired", "Failed"}


def source_id(dao: str, pid: int) -> str:
    return f"sputnik:{dao}#{pid}"


@dataclass(frozen=True)
class ReviewContext:
    dao: str
    target: dict[str, Any]  # redacted: no status / votes
    cutoff_ns: int  # target submission time
    period_ns: int
    decimals: int
    # earlier proposals, each with "status" only if visible at cutoff
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def target_id(self) -> str:
        return source_id(self.dao, self.target["id"])

    @cached_property
    def sources(self) -> dict[str, dict[str, Any]]:
        out = {source_id(self.dao, p["id"]): p for p in self.history}
        out[self.target_id] = self.target
        return out

    @cached_property
    def _decided(self) -> list[dict[str, Any]]:
        return [p for p in self.history if p.get("status") in DECIDED]

    def decided(self) -> list[dict[str, Any]]:
        return self._decided

    def same_token(self, ps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [p for p in ps if p["token_id"] == self.target["token_id"]]


def redact(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k not in ("status", "vote_counts")}


_PREPARED: dict[tuple[str, int, int], tuple[dict[int, dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]] = {}


def _prepare(snapshot: dict[str, Any]):
    key = (snapshot["dao"], snapshot.get("last_proposal_id", 0), len(snapshot["proposals"]))
    if key not in _PREPARED:
        if len(_PREPARED) > 16:
            _PREPARED.clear()
        props = {p["id"]: p for p in snapshot["proposals"]}
        pairs = [(p, redact(p)) for p in snapshot["proposals"]]
        _PREPARED[key] = (props, pairs)
    return _PREPARED[key]


def build_context(snapshot: dict[str, Any], proposal_id: int, decimals: dict[str, int]) -> ReviewContext:
    props, pairs = _prepare(snapshot)
    target = props[proposal_id]
    cutoff = target["submission_time"]
    period = snapshot["proposal_period_ns"]
    history = tuple(
        full if full["submission_time"] + period <= cutoff else red
        for full, red in pairs
        if full["submission_time"] < cutoff and full["id"] != proposal_id
    )
    return ReviewContext(
        dao=snapshot["dao"],
        target=redact(target),
        cutoff_ns=cutoff,
        period_ns=period,
        decimals=decimals.get(target["token_id"], -1),
        history=history,
    )
