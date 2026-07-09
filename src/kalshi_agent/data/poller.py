import asyncio
import datetime as dt
import logging
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from kalshi_agent.config import Settings
from kalshi_agent.data.client import KalshiClient
from kalshi_agent.data.models import Market, PriceSnapshot
from kalshi_agent.data.store import init_db, make_engine, make_session_factory

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def series_ticker_from_event(event_ticker: str) -> str:
    """Kalshi tickers are SERIES-EVENTSUFFIX[-MARKETSUFFIX]; the series ticker
    is the prefix before the first hyphen. Verified against live series/market
    listings 2026-07-08 (e.g. KXFED, KXMVESPORTSMULTIGAMEEXTENDED)."""
    return event_ticker.split("-", 1)[0]


async def load_allowed_series(client: KalshiClient, categories: tuple[str, ...]) -> set[str]:
    """Resolve the target categories (build spec evidence base — see config.py
    target_categories) to a set of series tickers, since /markets only supports
    filtering by a single series_ticker, not by category."""
    data = await client.get_series()
    allowed = {s["ticker"] for s in data.get("series", []) if s.get("category") in categories}
    logger.info("resolved %d series across categories %s", len(allowed), categories)
    return allowed


def _db_size_bytes(database_url: str) -> int:
    if not database_url.startswith("sqlite"):
        return 0
    db_path = Path(urlparse(database_url).path.lstrip("/"))
    return db_path.stat().st_size if db_path.exists() else 0


async def sync_markets_and_snapshot(
    client: KalshiClient,
    session: Session,
    *,
    allowed_series: set[str],
    max_db_size_bytes: int,
    database_url: str,
    status: str = "open",
) -> list[str]:
    """Single paginated sweep over Kalshi's open markets: filters to the
    allowed series (evidence-backed categories), upserts market metadata, and
    records a price snapshot from the same listing payload — no extra
    per-market request needed, since /markets already returns top-of-book
    prices. Stops adding *new* tickers once max_db_size_bytes is exceeded,
    but still snapshots already-tracked ones for this cycle."""
    tickers: list[str] = []
    cursor: str | None = None
    skipped_new_over_cap = 0

    while True:
        params: dict[str, object] = {"status": status, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = await client.get_markets(**params)
        markets = page.get("markets", [])

        size_ok = _db_size_bytes(database_url) < max_db_size_bytes

        for m in markets:
            event_ticker = m.get("event_ticker", "")
            if series_ticker_from_event(event_ticker) not in allowed_series:
                continue

            ticker = m["ticker"]
            is_new = session.get(Market, ticker) is None
            if is_new and not size_ok:
                skipped_new_over_cap += 1
                continue

            session.merge(
                Market(
                    ticker=ticker,
                    event_ticker=event_ticker,
                    series_ticker=series_ticker_from_event(event_ticker),
                    title=m.get("title", ""),
                    resolution_rules=m.get("rules_primary"),
                    open_time=_parse_dt(m.get("open_time")),
                    close_time=_parse_dt(m.get("close_time")),
                    expiration_time=_parse_dt(m.get("expiration_time")),
                    status=m.get("status", ""),
                    result=m.get("result"),
                    raw=m,
                )
            )
            session.add(
                PriceSnapshot(
                    ticker=ticker,
                    ts=dt.datetime.now(dt.timezone.utc),
                    yes_bid=_parse_float(m.get("yes_bid_dollars")),
                    yes_ask=_parse_float(m.get("yes_ask_dollars")),
                    last_price=_parse_float(m.get("last_price_dollars")),
                    volume=_parse_float(m.get("volume_fp")),
                    open_interest=_parse_float(m.get("open_interest_fp")),
                )
            )
            tickers.append(ticker)

        session.commit()
        cursor = page.get("cursor")
        if not cursor or not markets:
            break

    if skipped_new_over_cap:
        logger.warning(
            "DB at/over %.0f MB cap — skipped %d new markets this cycle (existing markets still snapshotted)",
            max_db_size_bytes / 1_000_000,
            skipped_new_over_cap,
        )
    return tickers


async def run_poller(settings: Settings) -> None:
    engine = make_engine(settings)
    init_db(engine)
    session_factory = make_session_factory(engine)

    async with KalshiClient(settings) as client:
        allowed_series = await load_allowed_series(client, settings.target_categories)
        while True:
            with session_factory() as session:
                tickers = await sync_markets_and_snapshot(
                    client,
                    session,
                    allowed_series=allowed_series,
                    max_db_size_bytes=settings.max_db_size_bytes,
                    database_url=settings.database_url,
                )
                logger.info(
                    "tracked %d markets in target categories, db=%.1f MB",
                    len(tickers),
                    _db_size_bytes(settings.database_url) / 1_000_000,
                )
            await asyncio.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from kalshi_agent.config import settings

    asyncio.run(run_poller(settings))
