import tempfile
from pathlib import Path

from sqlalchemy import create_engine

from kalshi_agent.data.models import Market, PriceSnapshot
from kalshi_agent.data.poller import series_ticker_from_event, sync_markets_and_snapshot
from kalshi_agent.data.store import init_db, make_session_factory


def test_series_ticker_from_event():
    assert series_ticker_from_event("KXFED-25DEC-T4.50") == "KXFED"
    assert series_ticker_from_event("KXMVESPORTSMULTIGAMEEXTENDED-S20261BA1D295484") == "KXMVESPORTSMULTIGAMEEXTENDED"


class FakeClient:
    """Duck-types the KalshiClient.get_markets surface with a canned, single-page response."""

    def __init__(self, markets: list[dict]) -> None:
        self._markets = markets

    async def get_markets(self, **params):
        return {"markets": self._markets, "cursor": None}


def _market(ticker: str, event_ticker: str, **overrides) -> dict:
    base = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": "Test market",
        "status": "open",
        "yes_bid_dollars": "0.4500",
        "yes_ask_dollars": "0.4700",
        "last_price_dollars": "0.4600",
        "volume_fp": "120.00",
        "open_interest_fp": "500.00",
    }
    base.update(overrides)
    return base


async def _run(tmp_path: Path, markets: list[dict], allowed_series: set[str], max_db_size_bytes: int = 10**9):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(db_url, future=True)
    init_db(engine)
    Session = make_session_factory(engine)
    client = FakeClient(markets)

    with Session() as session:
        tickers = await sync_markets_and_snapshot(
            client,
            session,
            allowed_series=allowed_series,
            max_db_size_bytes=max_db_size_bytes,
            database_url=db_url,
        )
    return tickers, Session


async def test_filters_out_disallowed_categories(tmp_path):
    markets = [
        _market("KXFED-25DEC-T4.50", "KXFED-25DEC"),
        _market("KXMVESPORTSMULTIGAMEEXTENDED-S1-M1", "KXMVESPORTSMULTIGAMEEXTENDED-S1"),
    ]
    tickers, Session = await _run(tmp_path, markets, allowed_series={"KXFED"})

    assert tickers == ["KXFED-25DEC-T4.50"]
    with Session() as session:
        assert session.get(Market, "KXFED-25DEC-T4.50") is not None
        assert session.get(Market, "KXMVESPORTSMULTIGAMEEXTENDED-S1-M1") is None


async def test_price_fields_parsed_as_floats(tmp_path):
    markets = [_market("KXFED-25DEC-T4.50", "KXFED-25DEC")]
    _, Session = await _run(tmp_path, markets, allowed_series={"KXFED"})

    with Session() as session:
        snap = session.query(PriceSnapshot).filter_by(ticker="KXFED-25DEC-T4.50").one()
        assert snap.yes_bid == 0.45
        assert snap.yes_ask == 0.47
        assert snap.last_price == 0.46
        assert snap.volume == 120.0
        assert snap.open_interest == 500.0


async def test_series_ticker_derived_and_stored(tmp_path):
    markets = [_market("KXFED-25DEC-T4.50", "KXFED-25DEC")]
    _, Session = await _run(tmp_path, markets, allowed_series={"KXFED"})

    with Session() as session:
        market = session.get(Market, "KXFED-25DEC-T4.50")
        assert market.series_ticker == "KXFED"


async def test_new_markets_skipped_once_over_size_cap(tmp_path):
    markets = [_market("KXFED-25DEC-T4.50", "KXFED-25DEC")]
    tickers, Session = await _run(tmp_path, markets, allowed_series={"KXFED"}, max_db_size_bytes=0)

    assert tickers == []
    with Session() as session:
        assert session.get(Market, "KXFED-25DEC-T4.50") is None
