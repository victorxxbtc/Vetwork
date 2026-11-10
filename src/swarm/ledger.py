"""In-memory mirror of the contract's accounting, used by the demo and the eval.

Same rules as `contract/src/lib.rs`: backers buy shares of an agent's balance,
inference is charged against the balance, accepted work is paid from the job
escrow into the balance, and unspent bounty goes back to the poster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

YOCTO = 10**24


def near(x: float) -> int:
    return int(round(x * 100)) * YOCTO // 100


class LedgerError(ValueError):
    pass


@dataclass
class AgentBook:
    balance: int = 0
    total_shares: int = 0
    shares: dict[str, int] = field(default_factory=dict)
    earned: int = 0
    spent_inference: int = 0


@dataclass
class Ledger:
    agents: dict[str, AgentBook] = field(default_factory=dict)
    escrow: dict[int, int] = field(default_factory=dict)
    inference_payee_received: int = 0

    def register(self, agent: str) -> None:
        if agent in self.agents:
            raise LedgerError(f"{agent} already registered")
        self.agents[agent] = AgentBook()

    def fund(self, agent: str, backer: str, amount: int) -> int:
        if amount <= 0:
            raise LedgerError("deposit must be positive")
        b = self.agents[agent]
        minted = amount if b.total_shares == 0 or b.balance == 0 else amount * b.total_shares // b.balance
        if minted == 0:
            raise LedgerError("deposit too small to mint a share")
        b.balance += amount
        b.total_shares += minted
        b.shares[backer] = b.shares.get(backer, 0) + minted
        return minted

    def withdraw(self, agent: str, backer: str, shares: int) -> int:
        b = self.agents[agent]
        if shares <= 0 or b.shares.get(backer, 0) < shares:
            raise LedgerError("not enough shares")
        out = shares * b.balance // b.total_shares
        b.shares[backer] -= shares
        b.total_shares -= shares
        b.balance -= out
        return out

    def value_of(self, agent: str, backer: str) -> int:
        b = self.agents[agent]
        return b.shares.get(backer, 0) * b.balance // b.total_shares if b.total_shares else 0

    def charge_inference(self, agent: str, amount: int) -> None:
        b = self.agents[agent]
        if amount > b.balance:
            raise LedgerError(f"{agent} cannot pay for inference")
        b.balance -= amount
        b.spent_inference += amount
        self.inference_payee_received += amount

    def post_job(self, job_id: int, bounty: int) -> None:
        if job_id in self.escrow:
            raise LedgerError("job exists")
        self.escrow[job_id] = bounty

    def pay(self, job_id: int, agent: str, amount: int) -> None:
        if amount > self.escrow[job_id]:
            raise LedgerError("escrow exhausted")
        self.escrow[job_id] -= amount
        b = self.agents[agent]
        b.balance += amount
        b.earned += amount

    def refund(self, job_id: int) -> int:
        left = self.escrow[job_id]
        self.escrow[job_id] = 0
        return left
