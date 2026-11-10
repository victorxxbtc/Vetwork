"""Claims: the unit an agent asserts and the reviewer checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SECTIONS = ("history", "budget", "milestones", "treasury", "risk")

SECTION_TITLES = {
    "history": "Team's past grants and delivery",
    "budget": "Budget against peer proposals",
    "milestones": "Can the milestones be checked",
    "treasury": "Treasury impact",
    "risk": "Risks",
}


@dataclass
class Claim:
    text: str
    kind: str  # count | median_amount | sum_amount | budget_ratio | quote | duplicate | burst | absent | target_field
    value: Any
    sources: list[str]
    params: dict[str, Any] = field(default_factory=dict)
    # filled by the reviewer
    status: str = "unchecked"  # verified | flagged
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    section: str
    agent: str
    score: int  # 0..100, higher = stronger case to fund
    summary: str
    claims: list[Claim]
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["claims"] = [c.to_dict() for c in self.claims]
        return d
