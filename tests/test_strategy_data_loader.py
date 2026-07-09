import datetime as dt

from sqlalchemy import create_engine

from kalshi_agent.data.models import Market, PriceSnapshot
from kalshi_agent.data.store import init_db, make_session_factory
from kalshi_agent.strategy.data_loader import load_resolved_observations


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'obs.db'}", future=True)
    init_db(engine)
    return make_session_factory(engine)


def test_loads_resolved_observations_with_latest_price(tmp_path):
    Session = _session_factory(tmp_path)
    t0 = dt.datetime.now(dt.timezone.utc)
    t1 = t0 + dt.timedelta(hours=1)

    with Session() as session:
        session.add(Market(ticker="KXFED-1", event_ticker="KXFED", series_ticker="KXFED",
                            title="t", status="finalized", result="yes", raw={}))
        session.add(Market(ticker="KXFED-2", event_ticker="KXFED", series_ticker="KXFED",
                            title="t", status="open", result=None, raw={}))
        # Two snapshots for the same resolved ticker — older mid-trading price, then
        # a later one taken at/after settlement. Only the latest should be used.
        session.add(PriceSnapshot(ticker="KXFED-1", ts=t0, yes_bid=0.3, yes_ask=0.35, last_price=0.30, volume=1, open_interest=1))
        session.add(PriceSnapshot(ticker="KXFED-1", ts=t1, yes_bid=0.95, yes_ask=1.0, last_price=0.97, volume=1, open_interest=1))
        session.commit()

        observations = load_resolved_observations(session)

    assert observations == [(0.97, True)]


def test_excludes_unresolved_and_priceless_markets(tmp_path):
    Session = _session_factory(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)

    with Session() as session:
        session.add(Market(ticker="OPEN-1", event_ticker="OPEN", series_ticker="OPEN",
                            title="t", status="open", result=None, raw={}))
        session.add(Market(ticker="RESOLVED-NO-PRICE", event_ticker="X", series_ticker="X",
                            title="t", status="finalized", result="no", raw={}))
        session.add(PriceSnapshot(ticker="OPEN-1", ts=now, yes_bid=0.5, yes_ask=0.5, last_price=0.5, volume=1, open_interest=1))
        session.commit()

        observations = load_resolved_observations(session)

    assert observations == []
