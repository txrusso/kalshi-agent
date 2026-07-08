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
