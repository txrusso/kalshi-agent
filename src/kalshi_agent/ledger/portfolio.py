import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from kalshi_agent.data.models import Fill, Market
from kalshi_agent.risk.guardrails import PortfolioState


@dataclass
class PositionInfo:
    ticker: str
    side: str
    count: int
    avg_price: float


def compute_positions(session: Session, *, mode: str) -> dict[str, PositionInfo]:
    """Event-sourced net position per (ticker, side) from the Fill log —
    derived on read rather than mutated in place, so there's no separate
    position-tracking state that can drift out of sync with the fill history."""
    positions: dict[str, PositionInfo] = {}
    fills = session.scalars(select(Fill).where(Fill.mode == mode).order_by(Fill.ts)).all()

    running_cost: dict[str, float] = {}
    running_count: dict[str, int] = {}

    for fill in fills:
        key = f"{fill.ticker}:{fill.side}"
        signed = fill.count if fill.action == "buy" else -fill.count
        running_count[key] = running_count.get(key, 0) + signed
        running_cost[key] = running_cost.get(key, 0.0) + signed * fill.price

    for key, count in running_count.items():
        if count == 0:
            continue
        ticker, side = key.split(":")
        avg_price = running_cost[key] / count if count else 0.0
        positions[key] = PositionInfo(ticker=ticker, side=side, count=count, avg_price=avg_price)

    return positions


def _net_cash_flow(fills: list[Fill]) -> float:
    total = 0.0
    for fill in fills:
        notional = fill.price * fill.count
        total += notional if fill.action == "sell" else -notional
        total -= fill.fee
    return total


def compute_cash_balance(session: Session, *, mode: str, starting_balance: float) -> float:
    """Available cash = starting balance + all-time net cash flow from fills.
    Money spent on still-open positions is already netted out; this is
    deliberately cash-available, not equity (mark-to-market of open positions
    is a dashboard concern, not a guardrail one)."""
    fills = session.scalars(select(Fill).where(Fill.mode == mode)).all()
    return starting_balance + _net_cash_flow(list(fills))


def compute_portfolio_state(session: Session, *, mode: str, balance: float) -> PortfolioState:
    positions = compute_positions(session, mode=mode)

    exposure_by_ticker: dict[str, float] = {}
    for pos in positions.values():
        exposure_by_ticker[pos.ticker] = exposure_by_ticker.get(pos.ticker, 0.0) + abs(pos.count * pos.avg_price)

    tickers = list(exposure_by_ticker.keys())
    event_by_ticker: dict[str, str] = {}
    if tickers:
        rows = session.execute(select(Market.ticker, Market.event_ticker).where(Market.ticker.in_(tickers))).all()
        event_by_ticker = dict(rows)

    exposure_by_event: dict[str, float] = {}
    for ticker, exposure in exposure_by_ticker.items():
        event = event_by_ticker.get(ticker, ticker)
        exposure_by_event[event] = exposure_by_event.get(event, 0.0) + exposure

    today = dt.datetime.now(dt.timezone.utc).date()
    todays_fills = session.scalars(
        select(Fill).where(Fill.mode == mode, Fill.ts >= dt.datetime.combine(today, dt.time.min, dt.timezone.utc))
    ).all()
    # Approximate daily P&L as net cash flow from today's fills — not full
    # FIFO cost-basis P&L, but sufficient as a circuit-breaker heuristic
    # (build spec §6).
    daily_pnl = _net_cash_flow(list(todays_fills))

    return PortfolioState(
        balance=balance,
        exposure_by_ticker=exposure_by_ticker,
        exposure_by_event=exposure_by_event,
        daily_pnl=daily_pnl,
    )
