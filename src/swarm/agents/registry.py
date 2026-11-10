"""Agent registry: who is in the swarm, what they read, and who funds them.

Anyone can add an agent: a name (it becomes `<name>.<swarm-account>`), a
section it bids on, a prompt, a knowledge base, a tool list and a NEAR
deposit. The deposit pays for the agent's inference; job revenue tops it up;
backers own shares of the balance.

The seed below is the demo swarm. Every account and amount in it is demo
data. `quickcheck` is intentionally naive: its knowledge base is a current
full export of the DAO, so it can see votes that had not happened yet. It is
cheap, so it often wins the history auction, and the reviewer is what stops
it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .claims import SECTIONS

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")

KNOWLEDGE_BASES = {
    "dated-context": "Sputnik proposals visible at the target's submission time (outcomes only after a full voting period)",
    "full-export": "A current export of every proposal in the DAO, outcomes included",
}
TOOLS = {
    "sputnik.history": "Proposals by the same proposer or receiver, with outcomes",
    "sputnik.peers": "Approved transfers in the same token, for a benchmark",
    "sputnik.outflow": "Approved outflow over a trailing window",
    "text.extract": "Links, dates and numeric targets quoted from the proposal text",
    "risk.rules": "Duplicate text, request bursts, implicit receivers, thin descriptions",
    "nearai.chat": "NEAR AI Cloud chat completion (OpenAI-compatible)",
}
DEFAULT_MODEL = "Qwen/Qwen3.8-27B"


@dataclass
class AgentSpec:
    name: str
    title: str
    section: str
    prompt: str
    knowledge_base: str = "dated-context"
    tools: list[str] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    base_price_near: float = 0.25
    params: dict[str, Any] = field(default_factory=dict)
    requires_llm: bool = False
    creator: str = ""

    def account(self, swarm_account: str) -> str:
        return f"{self.name}.{swarm_account}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate(spec: AgentSpec) -> list[str]:
    errs = []
    if not NAME_RE.match(spec.name):
        errs.append("name must be 2-32 chars of a-z, 0-9 and '-' (it becomes a NEAR sub-account)")
    if spec.section not in SECTIONS:
        errs.append(f"section must be one of {', '.join(SECTIONS)}")
    if spec.knowledge_base not in KNOWLEDGE_BASES:
        errs.append("unknown knowledge base")
    if not spec.prompt.strip() or len(spec.prompt) > 2000:
        errs.append("prompt must be 1-2000 characters")
    unknown = [t for t in spec.tools if t not in TOOLS]
    if unknown:
        errs.append(f"unknown tools: {', '.join(unknown)}")
    if not 0.01 <= spec.base_price_near <= 10:
        errs.append("bid price must be between 0.01 and 10 NEAR")
    return errs


SEED_AGENTS: list[AgentSpec] = [
    AgentSpec(
        "ledger", "Ledger Clerk", "history",
        "You audit a grant team's record. Report how many earlier requests from this proposer or to this "
        "receiver the DAO approved or rejected, using only outcomes that were final when the request was filed.",
        tools=["sputnik.history"], base_price_near=0.30,
    ),
    AgentSpec(
        "quickcheck", "Quickcheck", "history",
        "Fast track-record lookup over the DAO export.",
        knowledge_base="full-export", tools=["sputnik.history"], base_price_near=0.16,
        params={"look_ahead": True},
    ),
    AgentSpec(
        "comparables", "Comparables", "budget",
        "Compare the requested amount with the median of approved requests in the same token over the past year.",
        tools=["sputnik.peers"], base_price_near=0.26, params={"window_days": 365},
    ),
    AgentSpec(
        "comparables-90", "Comparables 90", "budget",
        "Compare the requested amount with approved requests in the same token over the past 90 days.",
        tools=["sputnik.peers"], base_price_near=0.24, params={"window_days": 90},
    ),
    AgentSpec(
        "milestone-reader", "Milestone Reader", "milestones",
        "List what in the proposal could be checked later: links, dates and numeric targets. Quote them exactly.",
        tools=["text.extract"], base_price_near=0.20,
    ),
    AgentSpec(
        "milestone-llm", "Milestone LLM", "milestones",
        "Read the proposal and return every checkable milestone as an exact quote.",
        tools=["text.extract", "nearai.chat"], base_price_near=0.12, requires_llm=True,
    ),
    AgentSpec(
        "treasurer", "Treasurer", "treasury",
        "Size the request against the DAO's approved outflow in the same token over the past 90 days.",
        tools=["sputnik.outflow"], base_price_near=0.15, params={"window_days": 90},
    ),
    AgentSpec(
        "runway", "Runway", "treasury",
        "Size the request against the DAO's approved outflow in the same token over the past 180 days.",
        tools=["sputnik.outflow"], base_price_near=0.17, params={"window_days": 180},
    ),
    AgentSpec(
        "redteam", "Red Team", "risk",
        "Look for duplicate filings, request bursts, thin descriptions and anonymous receivers.",
        tools=["risk.rules"], base_price_near=0.22,
    ),
    AgentSpec(
        "sentinel", "Sentinel", "risk",
        "Flag anything that would make a careful council member ask for more information.",
        tools=["risk.rules"], base_price_near=0.27,
    ),
]

# Demo backers (fictional testnet accounts) and their deposits, in NEAR.
SEED_BACKING: list[tuple[str, str, float]] = [
    ("ledger", "backer-ada.testnet", 4.0),
    ("ledger", "backer-kofi.testnet", 2.0),
    ("quickcheck", "backer-lin.testnet", 1.5),
    ("comparables", "backer-ada.testnet", 3.0),
    ("comparables-90", "backer-sol.testnet", 2.5),
    ("milestone-reader", "backer-kofi.testnet", 3.0),
    ("milestone-llm", "backer-lin.testnet", 2.0),
    ("treasurer", "backer-sol.testnet", 2.0),
    ("runway", "backer-mei.testnet", 2.0),
    ("redteam", "backer-mei.testnet", 3.5),
    ("sentinel", "backer-ada.testnet", 2.0),
]
