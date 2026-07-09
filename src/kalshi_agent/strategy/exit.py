import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from kalshi_agent.data.models import LatestPrice, Market, Order
from kalshi_agent.ledger.portfolio import compute_positions
from kalshi_agent.risk.guardrails import OrderRequest


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str | None = None


def evaluate_alpha_realized_exit(
    *,
    entry_price: float,
    fair_value_at_entry: float,
    current_price: float,
    alpha_realized_exit_fraction: float,
) -> ExitDecision:
    """User directive 2026-07-09: for long-term positions, exit once the
    current price has captured this fraction of the edge that existed at
    entry, rather than holding to resolution. All three prices must already
    be in the position's own entry-side terms (see OrderRequest.side) — for
    a "no" position that means (1 - yes_price) throughout, both here and by
    the caller."""
    edge_at_entry = fair_value_at_entry - entry_price
    if edge_at_entry <= 0:
        # No edge existed at entry (shouldn't happen for a real trade, but
        # guards div-by-zero / nonsensical negative-edge positions).
        return ExitDecision(should_exit=False)

    captured_fraction = (current_price - entry_price) / edge_at_entry
    if captured_fraction >= alpha_realized_exit_fraction:
        return ExitDecision(
            should_exit=True,
            reason=f"captured {captured_fraction:.0%} of entry edge ({edge_at_entry:.3f}) — alpha realized",
        )
    return ExitDecision(should_exit=False)


def find_positions_to_exit(session: Session, settings) -> list[OrderRequest]:
    """Scans open PAPER positions for long-term ones whose current price has
    realized enough of the edge estimated at entry, and returns sell orders
    to close them. MVP simplification: assumes one entry order per open
    ticker+side position (no partial-exit/re-entry averaging yet) — takes
    the most recent filled buy order for that ticker+side as the entry
    reference. Short-term positions are left alone; they're simply held to
    expiry, per the 2026-07-09 user directive."""
    positions = compute_positions(session, mode="PAPER")
    exit_orders: list[OrderRequest] = []

    for pos in positions.values():
        if pos.count <= 0:
            continue

        entry_order = session.scalars(
            select(Order)
            .where(Order.ticker == pos.ticker, Order.side == pos.side, Order.action == "buy", Order.status == "filled")
            .order_by(Order.created_ts.desc())
        ).first()
        if entry_order is None or entry_order.time_horizon != "long" or entry_order.fair_value_at_entry is None:
            continue

        latest = session.get(LatestPrice, pos.ticker)
        if latest is None or latest.last_price is None:
            continue

        current_price_yes = latest.last_price
        current_price = current_price_yes if pos.side == "yes" else 1 - current_price_yes
        if not 0 < current_price < 1:
            continue

        decision = evaluate_alpha_realized_exit(
            entry_price=entry_order.price,
            fair_value_at_entry=entry_order.fair_value_at_entry,
            current_price=current_price,
            alpha_realized_exit_fraction=settings.alpha_realized_exit_fraction,
        )
        if decision.should_exit:
            market = session.get(Market, pos.ticker)
            event_ticker = market.event_ticker if market is not None else pos.ticker
            exit_orders.append(
                OrderRequest(
                    ticker=pos.ticker,
                    event_ticker=event_ticker,
                    side=pos.side,
                    action="sell",
                    price=current_price,
                    count=pos.count,
                    mode="PAPER",
                    order_type="limit",
                )
            )

    return exit_orders
