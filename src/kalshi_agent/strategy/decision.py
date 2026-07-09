import datetime as dt

from kalshi_agent.risk.guardrails import OrderRequest
from kalshi_agent.risk.sizing import kelly_contracts, net_edge_per_contract
from kalshi_agent.strategy.horizon import classify_horizon
from kalshi_agent.strategy.signals import SignalResult


def build_order_request(
    *,
    ticker: str,
    event_ticker: str,
    price: float,
    signal: SignalResult,
    bankroll: float,
    settings,
    expiration_time: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> OrderRequest | None:
    """Ties a Signal's output to a sized order (build spec §4-5): net-edge gate
    checked on both sides (buy YES vs. buy NO), fractional-Kelly sizing scaled
    by the signal's own confidence, maker-style limit order by default — S1
    "Maker not Taker" is the default execution style here, not a standalone
    strategy. Returns None if there's no opinion, no edge past the threshold,
    or the sized position rounds down to zero contracts.

    Long-dated trades (see classify_horizon) require settings.long_term_edge_
    multiplier times the normal edge bar — a soft bias toward shorter-term
    activity per the 2026-07-09 user directive, not a hard cutoff, since the
    bias itself is largest for long-dated contracts."""
    if signal.fair_value is None:
        return None

    horizon = classify_horizon(expiration_time, short_term_horizon_days=settings.short_term_horizon_days, now=now)
    effective_min_edge = settings.min_net_edge_dollars
    if horizon == "long":
        effective_min_edge *= settings.long_term_edge_multiplier

    yes_edge = net_edge_per_contract(signal.fair_value, price, is_taker=False)
    no_edge = net_edge_per_contract(1 - signal.fair_value, 1 - price, is_taker=False)

    if yes_edge > effective_min_edge and yes_edge >= no_edge:
        side, entry_price, fair_value_for_side = "yes", price, signal.fair_value
    elif no_edge > effective_min_edge:
        side, entry_price, fair_value_for_side = "no", 1 - price, 1 - signal.fair_value
    else:
        return None

    count = kelly_contracts(
        fair_value_for_side,
        entry_price,
        bankroll=bankroll,
        kelly_fraction=settings.kelly_fraction * signal.confidence,
        max_fraction_of_bankroll=settings.per_market_max_fraction,
    )
    if count <= 0:
        return None

    return OrderRequest(
        ticker=ticker,
        event_ticker=event_ticker,
        side=side,
        action="buy",
        price=entry_price,
        count=count,
        mode=settings.mode,
        order_type="limit",
        fair_value_at_entry=fair_value_for_side,
        time_horizon=horizon,
    )
