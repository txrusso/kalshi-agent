import tempfile
from pathlib import Path

from sqlalchemy import create_engine

from kalshi_agent.data.models import LatestPrice, Market, PriceSnapshot
from kalshi_agent.data.poller import (
    _effective_size_cap,
    backfill_settled_markets,
    series_ticker_from_event,
    sync_latest_prices,
    sync_markets_and_snapshot,
)
from kalshi_agent.data.store import init_db, make_session_factory


def test_effective_size_cap_subtracts_safety_margin():
    assert _effective_size_cap(500_000_000) == 490_000_000


def test_effective_size_cap_floors_at_zero():
    assert _effective_size_cap(5_000_000) == 0


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


class FakePerSeriesClient:
    """Duck-types get_markets(series_ticker=..., status=..., ...) -> one
    canned page per series ticker, and records every call for assertions."""

    def __init__(self, markets_by_series: dict[str, list[dict]]) -> None:
        self._markets_by_series = markets_by_series
        self.calls: list[dict] = []

    async def get_markets(self, **params):
        self.calls.append(params)
        markets = self._markets_by_series.get(params.get("series_ticker"), [])
        return {"markets": markets, "cursor": None}


async def test_backfill_settled_queries_per_series_with_result(tmp_path):
    markets_by_series = {
        "KXFED": [_market("KXFED-25DEC-T4.50", "KXFED-25DEC", status="finalized", result="yes")],
        "KXCPI": [_market("KXCPI-25DEC-T1", "KXCPI-25DEC", status="finalized", result="no")],
    }
    client = FakePerSeriesClient(markets_by_series)
    db_url = f"sqlite:///{tmp_path / 'settled.db'}"
    engine = create_engine(db_url, future=True)
    init_db(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        tickers = await backfill_settled_markets(
            client, session, allowed_series={"KXFED", "KXCPI"}, max_db_size_bytes=10**9, database_url=db_url
        )

    assert set(tickers) == {"KXFED-25DEC-T4.50", "KXCPI-25DEC-T1"}
    assert all(c["status"] == "settled" and c["mve_filter"] == "exclude" for c in client.calls)
    assert {c["series_ticker"] for c in client.calls} == {"KXFED", "KXCPI"}

    with Session() as session:
        assert session.get(Market, "KXFED-25DEC-T4.50").result == "yes"
        assert session.get(Market, "KXCPI-25DEC-T1").result == "no"


async def test_backfill_settled_stops_when_over_size_cap(tmp_path):
    markets_by_series = {
        "KXFED": [_market("KXFED-25DEC-T4.50", "KXFED-25DEC", status="finalized", result="yes")],
    }
    client = FakePerSeriesClient(markets_by_series)
    db_url = f"sqlite:///{tmp_path / 'settled.db'}"
    engine = create_engine(db_url, future=True)
    init_db(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        tickers = await backfill_settled_markets(
            client, session, allowed_series={"KXFED"}, max_db_size_bytes=0, database_url=db_url
        )

    assert tickers == []
    assert client.calls == []


class FakePaginatedSeriesClient:
    """One series with two pages, to test that the size cap is rechecked
    between pages within a single series, not just between series."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get_markets(self, **params):
        self.calls.append(params)
        if not params.get("cursor"):
            return {
                "markets": [_market("KXFED-25DEC-T1", "KXFED-25DEC", status="finalized", result="yes")],
                "cursor": "page2",
            }
        return {
            "markets": [_market("KXFED-25DEC-T2", "KXFED-25DEC", status="finalized", result="no")],
            "cursor": None,
        }


async def test_backfill_settled_stops_mid_series_between_pages(tmp_path, monkeypatch):
    """Regression test: size cap used to be checked only once per series, so a
    series with many pages could blow past the cap before the next series-level
    check caught it (real overshoot: 512.5MB vs a 500MB cap). Simulate DB growth
    via a fake size function so the first page pushes it over the cap, and
    assert the second page of the *same* series is never fetched."""
    import kalshi_agent.data.poller as poller_module

    sizes = iter([0, 100])  # before page 1: under cap; before page 2: over cap
    monkeypatch.setattr(poller_module, "_db_size_bytes", lambda database_url: next(sizes))

    client = FakePaginatedSeriesClient()
    db_url = f"sqlite:///{tmp_path / 'settled.db'}"
    engine = create_engine(db_url, future=True)
    init_db(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        # +10_000_050 rather than 50 directly: the safety margin subtracts
        # 10MB from the raw cap before comparing (see _effective_size_cap).
        tickers = await backfill_settled_markets(
            client, session, allowed_series={"KXFED"}, max_db_size_bytes=10_000_050, database_url=db_url
        )

    assert tickers == ["KXFED-25DEC-T1"]
    assert len(client.calls) == 1  # second page of KXFED never fetched


async def test_sync_latest_prices_upserts_not_appends(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    engine = create_engine(db_url, future=True)
    init_db(engine)
    Session = make_session_factory(engine)

    market_v1 = [_market("KXFED-25DEC-T1", "KXFED-25DEC", yes_bid_dollars="0.4000")]
    market_v2 = [_market("KXFED-25DEC-T1", "KXFED-25DEC", yes_bid_dollars="0.5500")]

    with Session() as session:
        await sync_latest_prices(FakeClient(market_v1), session, allowed_series={"KXFED"})
    with Session() as session:
        await sync_latest_prices(FakeClient(market_v2), session, allowed_series={"KXFED"})

    with Session() as session:
        rows = session.query(LatestPrice).filter_by(ticker="KXFED-25DEC-T1").all()
        assert len(rows) == 1  # upserted, not a second row
        assert rows[0].yes_bid == 0.55

        # The append-only history table must be completely untouched by this path.
        assert session.query(PriceSnapshot).count() == 0


async def test_sync_latest_prices_filters_by_category_like_the_history_version(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    engine = create_engine(db_url, future=True)
    init_db(engine)
    Session = make_session_factory(engine)

    markets = [
        _market("KXFED-25DEC-T1", "KXFED-25DEC"),
        _market("KXMVESPORTSMULTIGAMEEXTENDED-S1-M1", "KXMVESPORTSMULTIGAMEEXTENDED-S1"),
    ]
    with Session() as session:
        tickers = await sync_latest_prices(FakeClient(markets), session, allowed_series={"KXFED"})

    assert tickers == ["KXFED-25DEC-T1"]
