"""Commit–reveal sealed-bid auction for review sections.

Mirrors the contract in `contract/src/lib.rs` byte for byte: the commitment is
hex(sha256("{agent}:{job_id}:{section}:{price_yocto}:{salt}")). A bid that is
never revealed, or revealed with a different price or salt, does not count.
Lowest revealed price at or under the section cap wins; ties go to the earlier
commit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

YOCTO = 10**24


def commitment(agent: str, job_id: int, section: str, price_yocto: int, salt: str) -> str:
    preimage = f"{agent}:{job_id}:{section}:{price_yocto}:{salt}"
    return hashlib.sha256(preimage.encode()).hexdigest()


@dataclass
class Commit:
    agent: str
    section: str
    digest: str
    seq: int
    revealed_price: int | None = None


@dataclass
class SectionResult:
    section: str
    winner: str | None
    price: int
    revealed: int
    committed: int


class AuctionError(ValueError):
    pass


@dataclass
class SealedAuction:
    job_id: int
    sections: list[str]
    cap_per_section: int
    phase: str = "commit"  # commit -> reveal -> closed
    commits: dict[tuple[str, str], Commit] = field(default_factory=dict)
    _seq: int = 0

    def commit(self, agent: str, section: str, digest: str) -> None:
        if self.phase != "commit":
            raise AuctionError("commit phase is over")
        if section not in self.sections:
            raise AuctionError(f"unknown section {section!r}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AuctionError("commitment must be 64 lowercase hex chars")
        key = (agent, section)
        if key in self.commits:
            raise AuctionError("agent already committed to this section")
        self._seq += 1
        self.commits[key] = Commit(agent, section, digest, self._seq)

    def start_reveal(self) -> None:
        if self.phase != "commit":
            raise AuctionError("not in commit phase")
        self.phase = "reveal"

    def reveal(self, agent: str, section: str, price_yocto: int, salt: str) -> bool:
        if self.phase != "reveal":
            raise AuctionError("not in reveal phase")
        c = self.commits.get((agent, section))
        if c is None:
            raise AuctionError("no commitment")
        if c.revealed_price is not None:
            raise AuctionError("already revealed")
        if commitment(agent, self.job_id, section, price_yocto, salt) != c.digest:
            return False
        c.revealed_price = price_yocto
        return True

    def close(self) -> list[SectionResult]:
        if self.phase != "reveal":
            raise AuctionError("not in reveal phase")
        self.phase = "closed"
        out = []
        for s in self.sections:
            entries = [c for c in self.commits.values() if c.section == s]
            valid = [
                c
                for c in entries
                if c.revealed_price is not None and 0 < c.revealed_price <= self.cap_per_section
            ]
            best = min(valid, key=lambda c: (c.revealed_price, c.seq), default=None)
            out.append(
                SectionResult(
                    section=s,
                    winner=best.agent if best else None,
                    price=best.revealed_price if best else 0,
                    revealed=sum(1 for c in entries if c.revealed_price is not None),
                    committed=len(entries),
                )
            )
        return out
