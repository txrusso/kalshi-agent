from dataclasses import dataclass, field
from typing import Literal


@dataclass
class OrderRequest:
    ticker: str
    event_ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    price: float  # dollars, (0, 1)
    count: int
    mode: Literal["PAPER", "LIVE"]
    order_type: Literal["limit", "market"] = "limit"


@dataclass
class PortfolioState:
    balance: float
    exposure_by_ticker: dict[str, float] = field(default_factory=dict)
    exposure_by_event: dict[str, float] = field(default_factory=dict)
    daily_pnl: float = 0.0


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str | None = None

    @staticmethod
    def ok() -> "GuardrailResult":
        return GuardrailResult(allowed=True)

    @staticmethod
    def reject(reason: str) -> "GuardrailResult":
        return GuardrailResult(allowed=False, reason=reason)


def check_order(request: OrderRequest, portfolio: PortfolioState, settings) -> GuardrailResult:
    """Build spec §6 guardrails, checked in order, first violation wins.
    Every one of these must pass before an order — PAPER or LIVE — is placed."""

    if not settings.trading_enabled:
        return GuardrailResult.reject("master enable switch (trading_enabled) is off")

    if request.mode == "LIVE" and not settings.live_armed:
        return GuardrailResult.reject("LIVE mode requires explicit live_armed=True")

    if not 0.01 <= request.price <= 0.99:
        return GuardrailResult.reject(f"price {request.price} outside 1-99c sanity range")

    if request.count <= 0:
        return GuardrailResult.reject(f"count {request.count} must be positive")

    if request.count > settings.max_contracts_per_order:
        return GuardrailResult.reject(
            f"count {request.count} exceeds max_contracts_per_order ({settings.max_contracts_per_order})"
        )

    notional = request.price * request.count

    if portfolio.balance <= 0:
        return GuardrailResult.reject("no balance available")

    per_market_cap = settings.per_market_max_fraction * portfolio.balance
    existing_market = portfolio.exposure_by_ticker.get(request.ticker, 0.0)
    if existing_market + notional > per_market_cap:
        return GuardrailResult.reject(
            f"per-market exposure {existing_market + notional:.2f} would exceed cap {per_market_cap:.2f} "
            f"({settings.per_market_max_fraction:.0%} of balance)"
        )

    aggregate_cap = settings.aggregate_max_fraction * portfolio.balance
    existing_total = sum(portfolio.exposure_by_ticker.values())
    if existing_total + notional > aggregate_cap:
        return GuardrailResult.reject(
            f"aggregate exposure {existing_total + notional:.2f} would exceed cap {aggregate_cap:.2f} "
            f"({settings.aggregate_max_fraction:.0%} of balance)"
        )

    correlated_cap = settings.correlated_event_max_fraction * portfolio.balance
    existing_event = portfolio.exposure_by_event.get(request.event_ticker, 0.0)
    if existing_event + notional > correlated_cap:
        return GuardrailResult.reject(
            f"correlated-event ({request.event_ticker}) exposure {existing_event + notional:.2f} would "
            f"exceed cap {correlated_cap:.2f} ({settings.correlated_event_max_fraction:.0%} of balance)"
        )

    daily_loss_limit = settings.daily_loss_limit_fraction * portfolio.balance
    if portfolio.daily_pnl <= -daily_loss_limit:
        return GuardrailResult.reject(
            f"daily loss circuit breaker tripped: daily_pnl {portfolio.daily_pnl:.2f} <= "
            f"-{daily_loss_limit:.2f} ({settings.daily_loss_limit_fraction:.0%} of balance)"
        )

    return GuardrailResult.ok()
