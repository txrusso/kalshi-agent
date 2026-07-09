import asyncio
import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from kalshi_agent.config import Settings
from kalshi_agent.data.client import KalshiClient
from kalshi_agent.data.models import LatestPrice, Market
from kalshi_agent.data.poller import load_allowed_series, sync_latest_prices
from kalshi_agent.data.store import init_db, make_engine, make_session_factory
from kalshi_agent.execution.adapter import ExecutionAdapter
from kalshi_agent.execution.paper import PaperExecutionAdapter
from kalshi_agent.ledger.audit import log_event
from kalshi_agent.ledger.portfolio import compute_cash_balance
from kalshi_agent.strategy.backtest import deduplicate_by_event, load_observations_with_time
from kalshi_agent.strategy.calibration import build_calibration_curve
from kalshi_agent.strategy.decision import build_order_request
from kalshi_agent.strategy.exit import find_positions_to_exit
from kalshi_agent.strategy.signals import FavoriteLongshotSignal

logger = logging.getLogger(__name__)


def build_signal_from_history(session: Session, settings) -> FavoriteLongshotSignal:
    """Rebuilds the S2 calibration curve from every resolved market in the
    local store (deduplicated by event — see strategy/backtest.py for why
    that matters). This is the full historical set, not a train/test split —
    the split only exists to validate the approach in scripts/run_backtest.py;
    live trading should use every real data point available."""
    observations = load_observations_with_time(session)
    deduped = deduplicate_by_event(observations)
    curve = build_calibration_curve(
        [(o.price, o.resolved_yes) for o in deduped], bucket_width=settings.calibration_bucket_width
    )
    return FavoriteLongshotSignal(curve, min_samples=settings.calibration_min_samples)


def latest_open_market_candidates(session: Session) -> list[tuple[str, str, dt.datetime | None, float]]:
    """(ticker, event_ticker, expiration_time, current_price) for every
    currently open, tracked market — the candidate set for trading decisions.

    Kalshi's `/markets?status=open` query filter returns markets whose OWN
    `status` field reads "active", not "open" — "open" is only a valid query
    *parameter* value, never a value stored in the response body. Filtering
    on `Market.status == "open"` here silently matched zero rows in
    production (real DB check 2026-07-09: 0 markets with status='open',
    24,314 with status='active') even though the poller had been correctly
    tracking them all along — a genuine bug, not the risk-threshold finding."""
    rows = session.execute(
        select(Market.ticker, Market.event_ticker, Market.expiration_time, LatestPrice.last_price)
        .join(LatestPrice, LatestPrice.ticker == Market.ticker)
        .where(Market.status == "active")
    ).all()
    return [(ticker, event_ticker, expiration_time, price) for ticker, event_ticker, expiration_time, price in rows if price is not None]


async def run_trading_cycle(
    session_factory: sessionmaker,
    adapter: ExecutionAdapter,
    signal: FavoriteLongshotSignal,
    settings,
) -> int:
    """Evaluates every open tracked market against the signal and submits
    any order the decision layer produces. Returns the number accepted."""
    with session_factory() as session:
        cash_balance = compute_cash_balance(session, mode=settings.mode, starting_balance=settings.paper_starting_balance)
        candidates = latest_open_market_candidates(session)

    accepted = 0
    for ticker, event_ticker, expiration_time, price in candidates:
        if not 0 < price < 1:
            continue
        sig_result = signal.evaluate(price)
        if sig_result.fair_value is None:
            continue

        order_request = build_order_request(
            ticker=ticker,
            event_ticker=event_ticker,
            price=price,
            signal=sig_result,
            bankroll=cash_balance,
            settings=settings,
            expiration_time=expiration_time,
        )
        if order_request is None:
            continue

        client_order_id = f"auto-{ticker}-{uuid.uuid4().hex[:10]}"
        result = await adapter.submit_order(
            order_request,
            client_order_id=client_order_id,
            reason=f"S2 favorite-longshot: fair_value={sig_result.fair_value:.3f}, confidence={sig_result.confidence:.2f}",
        )
        if result.accepted:
            accepted += 1
            logger.info("order accepted: %s %s x%d @ %.2f", ticker, order_request.side, order_request.count, order_request.price)
            # Refresh balance after every fill: sizing later candidates in
            # this same cycle off the stale (larger) pre-fill balance
            # systematically oversizes them, so they then get rejected by
            # the per-market cap purely from staleness, not because they're
            # genuinely too large. Hit this for real 2026-07-09 -- 12 of 13
            # candidates in one cycle were rejected this way right after the
            # first fill dropped the true balance.
            with session_factory() as session:
                cash_balance = compute_cash_balance(session, mode=settings.mode, starting_balance=settings.paper_starting_balance)

    return accepted


async def run_exit_cycle(session_factory: sessionmaker, adapter: ExecutionAdapter, settings) -> int:
    """Checks open long-term positions for alpha-realization exits and
    submits the closing sell orders. Returns the number accepted."""
    with session_factory() as session:
        exit_requests = find_positions_to_exit(session, settings)

    closed = 0
    for order_request in exit_requests:
        client_order_id = f"exit-{order_request.ticker}-{uuid.uuid4().hex[:10]}"
        result = await adapter.submit_order(order_request, client_order_id=client_order_id, reason="alpha-realized exit")
        if result.accepted:
            closed += 1
            logger.info("exit accepted: %s %s x%d @ %.2f", order_request.ticker, order_request.side, order_request.count, order_request.price)

    return closed


async def run_agent_cycle(
    client: KalshiClient,
    session_factory: sessionmaker,
    adapter: ExecutionAdapter,
    allowed_series: set[str],
    signal: FavoriteLongshotSignal,
    settings,
) -> dict:
    """One full cycle: refresh data, then (if trading_enabled) evaluate new
    trades and check existing positions for exits. Returns a small summary
    dict for logging/testing — kept separate from the infinite loop in
    run_agent so it's directly testable."""
    with session_factory() as session:
        tracked = await sync_latest_prices(client, session, allowed_series=allowed_series)
        log_event(session, "data_refresh", details={"markets_tracked": len(tracked)})

    if not settings.trading_enabled:
        logger.info("trading_enabled=False -- data refreshed, skipping trading/exit this cycle")
        return {"markets_tracked": len(tracked), "orders_placed": 0, "exits_placed": 0, "trading_enabled": False}

    orders_placed = await run_trading_cycle(session_factory, adapter, signal, settings)
    exits_placed = await run_exit_cycle(session_factory, adapter, settings)
    return {
        "markets_tracked": len(tracked),
        "orders_placed": orders_placed,
        "exits_placed": exits_placed,
        "trading_enabled": True,
    }


async def run_agent(settings: Settings) -> None:
    """The full agent loop: data -> signal -> decision -> PAPER execution ->
    exit monitoring, repeating every poll_interval_seconds. LIVE trading is
    not possible here -- PaperExecutionAdapter never calls a real order-
    placement endpoint, and none exists in KalshiClient. Going live is a
    separate, explicitly human-armed step (build spec §6/§9), not something
    this loop can do by itself."""
    engine = make_engine(settings)
    init_db(engine)
    session_factory = make_session_factory(engine)
    adapter = PaperExecutionAdapter(session_factory, settings)

    async with KalshiClient(settings) as client:
        allowed_series = await load_allowed_series(client, settings.target_categories)

        with session_factory() as session:
            signal = build_signal_from_history(session, settings)
        last_calibration_refresh = dt.datetime.now(dt.timezone.utc)
        logger.info("calibration curve built from history")

        while True:
            cycle_start = dt.datetime.now(dt.timezone.utc)

            # Recalibrate daily -- the curve doesn't meaningfully change
            # minute to minute, and rebuilding it is a real query cost.
            if cycle_start - last_calibration_refresh > dt.timedelta(hours=24):
                with session_factory() as session:
                    signal = build_signal_from_history(session, settings)
                last_calibration_refresh = cycle_start
                logger.info("calibration curve refreshed")

            summary = await run_agent_cycle(client, session_factory, adapter, allowed_series, signal, settings)
            logger.info("cycle complete: %s", summary)

            elapsed = (dt.datetime.now(dt.timezone.utc) - cycle_start).total_seconds()
            await asyncio.sleep(max(0.0, settings.poll_interval_seconds - elapsed))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from kalshi_agent.config import settings

    asyncio.run(run_agent(settings))
