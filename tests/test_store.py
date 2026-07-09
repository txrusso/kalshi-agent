import datetime as dt

from kalshi_agent.data.models import Market, PriceSnapshot
from kalshi_agent.data.store import init_db, make_session_factory
from sqlalchemy import create_engine


def _in_memory_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return engine


def test_market_and_snapshot_roundtrip():
    engine = _in_memory_engine()
    Session = make_session_factory(engine)

    with Session() as session:
        session.add(
            Market(
                ticker="TEST-01",
                event_ticker="TEST",
                series_ticker="TESTSERIES",
                title="Test market",
                status="open",
                raw={"ticker": "TEST-01"},
            )
        )
        session.commit()

        session.add(
            PriceSnapshot(
                ticker="TEST-01",
                ts=dt.datetime.now(dt.timezone.utc),
                yes_bid=45,
                yes_ask=47,
                last_price=46,
                volume=100,
                open_interest=500,
            )
        )
        session.commit()

        market = session.get(Market, "TEST-01")
        assert market is not None
        assert market.title == "Test market"

        snapshot = session.query(PriceSnapshot).filter_by(ticker="TEST-01").one()
        assert snapshot.yes_bid == 45


def test_make_engine_enables_wal_mode_for_concurrent_access(tmp_path):
    from dataclasses import dataclass

    from kalshi_agent.data.store import make_engine

    @dataclass
    class FakeSettings:
        database_url: str

    settings = FakeSettings(database_url=f"sqlite:///{tmp_path / 'wal_test.db'}")
    engine = make_engine(settings)

    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()

    assert mode == "wal"
    assert timeout == 5000


def test_init_db_adds_missing_columns_to_existing_table(tmp_path):
    import sqlite3

    from sqlalchemy import text

    db_path = tmp_path / "drift.db"
    # Simulate an old schema: create the orders table without the two
    # columns that were added to the Order model after some DBs already
    # existed (the exact scenario that crashed the live agent 2026-07-09).
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE orders (
            client_order_id VARCHAR PRIMARY KEY,
            ticker VARCHAR NOT NULL,
            side VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            order_type VARCHAR NOT NULL,
            count INTEGER NOT NULL,
            status VARCHAR NOT NULL,
            mode VARCHAR NOT NULL,
            created_ts DATETIME NOT NULL,
            updated_ts DATETIME NOT NULL
        )
    """)
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    init_db(engine)  # should add fair_value_at_entry, time_horizon, etc. without erroring

    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(orders)")}
    assert "fair_value_at_entry" in cols
    assert "time_horizon" in cols

    # And the column should actually be usable, not just present.
    Session = make_session_factory(engine)
    with Session() as session:
        session.execute(text(
            "INSERT INTO orders (client_order_id, ticker, side, action, order_type, count, status, mode, "
            "created_ts, updated_ts, fair_value_at_entry, time_horizon) "
            "VALUES ('o1', 'T', 'yes', 'buy', 'limit', 1, 'filled', 'PAPER', '2026-01-01', '2026-01-01', 0.6, 'long')"
        ))
        session.commit()
        result = session.execute(text("SELECT fair_value_at_entry, time_horizon FROM orders WHERE client_order_id='o1'")).one()
        assert result == (0.6, "long")
