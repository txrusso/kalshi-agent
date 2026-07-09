import datetime as dt
from dataclasses import dataclass

from sqlalchemy import create_engine

from kalshi_agent.dashboard.data import (
    get_account_summary,
    get_data_collection_status,
    get_paper_performance,
    get_recent_audit_events,
    get_recent_orders,
    seconds_since,
)
from kalshi_agent.data.models import AuditLogEntry, Fill, Market, Order, PriceSnapshot
from kalshi_agent.data.store import init_db, make_session_factory


@dataclass
class FakeSettings:
    mode: str = "PAPER"
    trading_enabled: bool = True
    live_armed: bool = False
    paper_starting_balance: float = 1000.0
    database_url: str = "sqlite:///:memory:"
    max_db_size_bytes: int = 500_000_000
    target_categories: tuple = ("Politics", "Elections")


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dash.db'}", future=True)
    init_db(engine)
    return make_session_factory(engine)


def test_account_summary_reflects_paper_fills(tmp_path):
    Session = _session_factory(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        session.add(Order(client_order_id="o1", ticker="KXFED-25DEC-T4.50", side="yes", action="buy",
                           order_type="limit", price=0.45, count=10, status="filled", mode="PAPER",
                           strategy=None, reason=None, created_ts=now, updated_ts=now, raw=None))
        session.add(Fill(order_id="o1", ticker="KXFED-25DEC-T4.50", side="yes", action="buy",
                          price=0.45, count=10, fee=0.02, is_taker=False, mode="PAPER", ts=now, raw=None))
        session.commit()

    settings = FakeSettings()
    summary = get_account_summary(Session, settings, real_balance_dollars=20.0)

    assert summary["real_balance_dollars"] == 20.0
    assert summary["paper_balance_dollars"] == 1000.0 - 4.5 - 0.02
    assert summary["paper_positions"] == [{"ticker": "KXFED-25DEC-T4.50", "side": "yes", "count": 10, "avg_price": 0.45}]


def test_data_collection_status_counts_and_top_series(tmp_path):
    Session = _session_factory(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        for i in range(3):
            session.add(Market(ticker=f"KXFED-25DEC-T{i}", event_ticker="KXFED-25DEC", series_ticker="KXFED",
                                title="t", status="open", raw={}))
        session.add(Market(ticker="KXCPI-25DEC-T1", event_ticker="KXCPI-25DEC", series_ticker="KXCPI",
                            title="t", status="open", raw={}))
        session.add(PriceSnapshot(ticker="KXFED-25DEC-T0", ts=now, yes_bid=0.4, yes_ask=0.45, last_price=0.42, volume=1, open_interest=1))
        session.commit()

    settings = FakeSettings(database_url=f"sqlite:///{tmp_path / 'dash.db'}")
    status = get_data_collection_status(Session, settings)

    assert status["market_count"] == 4
    assert status["price_snapshot_count"] == 1
    assert status["top_series"][0] == {"series_ticker": "KXFED", "market_count": 3}
    assert status["last_snapshot_ts"] is not None


def test_recent_orders_and_audit_events_ordering(tmp_path):
    Session = _session_factory(tmp_path)
    t0 = dt.datetime.now(dt.timezone.utc)
    t1 = t0 + dt.timedelta(seconds=1)
    with Session() as session:
        session.add(Order(client_order_id="o1", ticker="A", side="yes", action="buy", order_type="limit",
                           price=0.5, count=1, status="filled", mode="PAPER", strategy=None, reason=None,
                           created_ts=t0, updated_ts=t0, raw=None))
        session.add(Order(client_order_id="o2", ticker="B", side="yes", action="buy", order_type="limit",
                           price=0.5, count=1, status="filled", mode="PAPER", strategy=None, reason=None,
                           created_ts=t1, updated_ts=t1, raw=None))
        session.add(AuditLogEntry(ts=t0, event_type="order_filled", ticker="A", details={}))
        session.add(AuditLogEntry(ts=t1, event_type="order_filled", ticker="B", details={}))
        session.commit()

    orders = get_recent_orders(Session, limit=10)
    assert [o["client_order_id"] for o in orders] == ["o2", "o1"]

    events = get_recent_audit_events(Session, limit=10)
    assert [e["ticker"] for e in events] == ["B", "A"]


def test_seconds_since_handles_none_and_naive_datetimes():
    assert seconds_since(None) is None

    # Simulate SQLite's known round-trip quirk: a UTC-aware datetime we wrote
    # ourselves comes back out naive (tzinfo stripped), but the instant it
    # represents is still UTC. Using local time here would test the wrong
    # scenario, since every write path in this codebase uses UTC explicitly.
    utc_30s_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
    naive_but_utc = utc_30s_ago.replace(tzinfo=None)
    result = seconds_since(naive_but_utc)
    assert result is not None
    assert 25 < result < 35


def _paper_buy_order(client_order_id: str, ticker: str, side: str, ts: dt.datetime) -> Order:
    return Order(
        client_order_id=client_order_id, kalshi_order_id=None, ticker=ticker, side=side, action="buy",
        order_type="limit", price=0.5, count=10, status="filled", mode="PAPER", strategy=None, reason=None,
        fair_value_at_entry=None, time_horizon=None, created_ts=ts, updated_ts=ts, raw=None,
    )


def test_paper_performance_computes_win_rate_from_resolved_markets(tmp_path):
    Session = _session_factory(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        session.add(Market(ticker="WIN-1", event_ticker="E1", series_ticker="S", title="t", status="finalized", result="yes", raw={}))
        session.add(Market(ticker="LOSE-1", event_ticker="E2", series_ticker="S", title="t", status="finalized", result="no", raw={}))
        session.add(Market(ticker="OPEN-1", event_ticker="E3", series_ticker="S", title="t", status="open", result=None, raw={}))
        session.add(_paper_buy_order("o1", "WIN-1", "yes", now))   # bought yes, resolved yes -> win
        session.add(_paper_buy_order("o2", "LOSE-1", "yes", now))  # bought yes, resolved no -> loss
        session.add(_paper_buy_order("o3", "OPEN-1", "yes", now))  # unresolved -> excluded
        session.commit()

    performance = get_paper_performance(Session)

    assert performance["resolved_trades"] == 2
    assert performance["wins"] == 1
    assert performance["win_rate"] == 0.5


def test_paper_performance_none_when_no_resolved_trades(tmp_path):
    Session = _session_factory(tmp_path)
    performance = get_paper_performance(Session)
    assert performance == {"resolved_trades": 0, "wins": 0, "win_rate": None}
