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


def compute_realized_pnl_today(session: Session, *, mode: str) -> float:
    """Realized P&L for the daily-loss circuit breaker: only SELL fills
    count as a gain or loss, valued against the position's average cost
    basis at the moment of sale — opening a position (a BUY) moves cash
    into an asset, it is not a loss. A BUY still realizes its fee as a real
    cost. Without this, _net_cash_flow (correct for cash-balance purposes)
    was reused for daily_pnl too, which counted the full notional of every
    BUY as an immediate "loss" — a single trade at a 10% per-market cap
    always exceeds a 5% daily-loss threshold, so the circuit breaker tripped
    after the very first trade of every day, every day. Hit this for real
    2026-07-09: one $99.84 buy showed as daily_pnl=-99.91, tripping the
    breaker and blocking every subsequent candidate that cycle.

    Replays the FULL fill history chronologically to track accurate running
    average cost (so today's sells are valued against their true entry
    cost, even if the entry was days ago), but only sums P&L for fills that
    happened today."""
    today = dt.datetime.now(dt.timezone.utc).date()
    day_start = dt.datetime.combine(today, dt.time.min, dt.timezone.utc)
    fills = session.scalars(select(Fill).where(Fill.mode == mode).order_by(Fill.ts)).all()

    running_count: dict[str, int] = {}
    running_cost: dict[str, float] = {}
    realized_today = 0.0

    for fill in fills:
        key = f"{fill.ticker}:{fill.side}"
        count_before = running_count.get(key, 0)
        cost_before = running_cost.get(key, 0.0)
        avg_cost = (cost_before / count_before) if count_before else 0.0
        # SQLite can hand back a naive datetime for a value written as UTC-
        # aware (see dashboard/data.py's seconds_since for the same quirk) —
        # every write path in this codebase uses datetime.now(UTC), so a
        # naive value here is always really UTC.
        fill_ts = fill.ts if fill.ts.tzinfo is not None else fill.ts.replace(tzinfo=dt.timezone.utc)
        is_today = fill_ts >= day_start

        if fill.action == "buy":
            running_count[key] = count_before + fill.count
            running_cost[key] = cost_before + fill.count * fill.price
            if is_today:
                realized_today -= fill.fee
        else:
            sold = min(fill.count, count_before)
            running_count[key] = count_before - sold
            running_cost[key] = cost_before - sold * avg_cost
            if is_today:
                realized_today += (fill.price - avg_cost) * sold - fill.fee

    return realized_today


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

    daily_pnl = compute_realized_pnl_today(session, mode=mode)

    return PortfolioState(
        balance=balance,
        exposure_by_ticker=exposure_by_ticker,
        exposure_by_event=exposure_by_event,
        daily_pnl=daily_pnl,
    )
