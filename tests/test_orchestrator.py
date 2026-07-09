import datetime as dt
from dataclasses import dataclass

from sqlalchemy import create_engine

from kalshi_agent.data.models import LatestPrice, Market
from kalshi_agent.data.store import init_db, make_session_factory
from kalshi_agent.execution.paper import PaperExecutionAdapter
from kalshi_agent.orchestrator import (
    latest_open_market_candidates,
    run_agent_cycle,
    run_exit_cycle,
    run_trading_cycle,
)
from kalshi_agent.strategy.calibration import build_calibration_curve
from kalshi_agent.strategy.signals import FavoriteLongshotSignal


@dataclass
class FakeSettings:
    mode: str = "PAPER"
    trading_enabled: bool = True
    live_armed: bool = False
    kelly_fraction: float = 0.125
    min_net_edge_dollars: float = 0.01  # low, so the well-populated bucket clears it in tests
    per_market_max_fraction: float = 1.0
    aggregate_max_fraction: float = 1.0
    correlated_event_max_fraction: float = 1.0
    daily_loss_limit_fraction: float = 1.0
    max_contracts_per_order: int = 10_000
    paper_starting_balance: float = 1000.0
    short_term_horizon_days: float = 7.0
    long_term_edge_multiplier: float = 2.0
    alpha_realized_exit_fraction: float = 0.7
    calibration_bucket_width: float = 0.05
    calibration_min_samples: int = 5


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'orch.db'}", future=True)
    init_db(engine)
    return make_session_factory(engine)


def _seed_open_market(Session, ticker, event_ticker, price, expiration_time=None):
    now = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        session.add(Market(ticker=ticker, event_ticker=event_ticker, series_ticker=event_ticker.split("-")[0],
                            title="t", status="open", expiration_time=expiration_time, raw={}))
        session.add(LatestPrice(ticker=ticker, ts=now, yes_bid=price, yes_ask=price, last_price=price, volume=1, open_interest=1))
        session.commit()


def _biased_signal():
    # A cheap, well-populated bucket that's genuinely overpriced: priced ~3c,
    # true win rate ~0% -> strong signal to fade (buy NO).
    obs = [(0.03, False)] * 10
    curve = build_calibration_curve(obs, bucket_width=0.05)
    return FavoriteLongshotSignal(curve, min_samples=5)


def test_latest_open_market_candidates_only_includes_open_with_price(tmp_path):
    Session = _session_factory(tmp_path)
    _seed_open_market(Session, "KXFED-1", "KXFED-E1", 0.45)
    with Session() as session:
        # A closed market shouldn't show up.
        session.add(Market(ticker="KXFED-2", event_ticker="KXFED-E2", series_ticker="KXFED",
                            title="t", status="closed", raw={}))
        session.commit()

    with Session() as session:
        candidates = latest_open_market_candidates(session)

    assert len(candidates) == 1
    assert candidates[0][0] == "KXFED-1"


async def test_run_trading_cycle_places_order_on_real_edge(tmp_path):
    Session = _session_factory(tmp_path)
    _seed_open_market(Session, "KXFED-1", "KXFED-E1", 0.03)
    adapter = PaperExecutionAdapter(Session, FakeSettings())
    signal = _biased_signal()

    accepted = await run_trading_cycle(Session, adapter, signal, FakeSettings())

    assert accepted == 1


async def test_run_trading_cycle_no_orders_when_trading_disabled_upstream():
    # run_trading_cycle itself doesn't check trading_enabled -- that's
    # run_agent_cycle's job, verified separately below.
    pass


async def test_run_exit_cycle_returns_zero_with_no_positions(tmp_path):
    Session = _session_factory(tmp_path)
    adapter = PaperExecutionAdapter(Session, FakeSettings())
    closed = await run_exit_cycle(Session, adapter, FakeSettings())
    assert closed == 0


class FakeKalshiClient:
    """Duck-types get_markets/get_series for the orchestrator's data-refresh step."""

    async def get_markets(self, **params):
        return {"markets": [], "cursor": None}


async def test_run_agent_cycle_skips_trading_when_disabled(tmp_path):
    Session = _session_factory(tmp_path)
    settings = FakeSettings(trading_enabled=False)
    adapter = PaperExecutionAdapter(Session, settings)
    signal = _biased_signal()

    summary = await run_agent_cycle(FakeKalshiClient(), Session, adapter, set(), signal, settings)

    assert summary["trading_enabled"] is False
    assert summary["orders_placed"] == 0
    assert summary["exits_placed"] == 0


async def test_run_agent_cycle_trades_when_enabled(tmp_path):
    Session = _session_factory(tmp_path)
    _seed_open_market(Session, "KXFED-1", "KXFED-E1", 0.03)
    settings = FakeSettings(trading_enabled=True)
    adapter = PaperExecutionAdapter(Session, settings)
    signal = _biased_signal()

    summary = await run_agent_cycle(FakeKalshiClient(), Session, adapter, set(), signal, settings)

    assert summary["trading_enabled"] is True
    assert summary["orders_placed"] == 1
