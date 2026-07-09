from abc import ABC, abstractmethod
from dataclasses import dataclass

from kalshi_agent.risk.guardrails import OrderRequest


@dataclass
class OrderResult:
    accepted: bool
    client_order_id: str
    reason: str | None = None  # rejection reason, or None if accepted
    filled_count: int = 0
    fill_price: float | None = None
    fee: float = 0.0


class ExecutionAdapter(ABC):
    """PAPER | LIVE adapter interface (build spec §2, §4) — strategy code
    submits the same OrderRequest regardless of which adapter is wired in."""

    @abstractmethod
    async def submit_order(self, request: OrderRequest, *, client_order_id: str, reason: str | None = None) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, client_order_id: str) -> bool: ...
