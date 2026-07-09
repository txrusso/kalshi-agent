import asyncio
import datetime as dt
import logging
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from kalshi_agent.config import Settings
from kalshi_agent.data.client import KalshiClient
from kalshi_agent.data.models import LatestPrice, Market, PriceSnapshot
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


# The cap is checked before fetching each page, but a single page (up to
# 1000 markets) can still push the file past the raw cap before the next
# check catches it — measured a real 2.1MB overshoot (502.1MB vs a 500MB
# cap) from exactly this. Subtracting a safety margin from the effective
# threshold keeps the actual file size under the true target.
_SIZE_CAP_SAFETY_MARGIN_BYTES = 10_000_000


def _effective_size_cap(max_db_size_bytes: int) -> int:
    return max(0, max_db_size_bytes - _SIZE_CAP_SAFETY_MARGIN_BYTES)


def _store_market_and_snapshot(session: Session, m: dict) -> str:
    """Upserts Market metadata + a PriceSnapshot from one /markets listing
    entry. Works for both open and settled markets — settled markets carry a
    `result` field alongside the same price fields (verified 2026-07-08)."""
    ticker = m["ticker"]
    event_ticker = m.get("event_ticker", "")
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
    return ticker


def _store_market_and_latest_price(session: Session, m: dict) -> str:
    """Upserts Market metadata + a LatestPrice row (bounded — one row per
    ticker, not append-only). Use this, not _store_market_and_snapshot, for
    any *repeating* sync loop — see LatestPrice's docstring for why."""
    ticker = m["ticker"]
    event_ticker = m.get("event_ticker", "")
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
    session.merge(
        LatestPrice(
            ticker=ticker,
            ts=dt.datetime.now(dt.timezone.utc),
            yes_bid=_parse_float(m.get("yes_bid_dollars")),
            yes_ask=_parse_float(m.get("yes_ask_dollars")),
            last_price=_parse_float(m.get("last_price_dollars")),
            volume=_parse_float(m.get("volume_fp")),
            open_interest=_parse_float(m.get("open_interest_fp")),
        )
    )
    return ticker


async def sync_latest_prices(
    client: KalshiClient,
    session: Session,
    *,
    allowed_series: set[str],
    status: str = "open",
) -> list[str]:
    """The repeating-safe counterpart to sync_markets_and_snapshot: same
    paginated sweep and category filter, but upserts LatestPrice (bounded)
    instead of appending PriceSnapshot rows forever. This is what continuous
    operation (run_poller, the orchestrator) should call — no size-cap logic
    needed here since upserting can't grow the table beyond one row per
    currently-tracked market."""
    tickers: list[str] = []
    cursor: str | None = None

    while True:
        params: dict[str, object] = {"status": status, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = await client.get_markets(**params)
        markets = page.get("markets", [])

        for m in markets:
            if series_ticker_from_event(m.get("event_ticker", "")) not in allowed_series:
                continue
            tickers.append(_store_market_and_latest_price(session, m))

        session.commit()
        cursor = page.get("cursor")
        if not cursor or not markets:
            break

    return tickers


async def sync_markets_and_snapshot(
    client: KalshiClient,
    session: Session,
    *,
    allowed_series: set[str],
    max_db_size_bytes: int,
    database_url: str,
    status: str = "open",
) -> list[str]:
    """Single paginated sweep over Kalshi's markets in the given status:
    filters to the allowed series (evidence-backed categories) client-side and
    upserts. Works well for `open` (~230k total, allowed categories interleaved
    throughout — verified 2026-07-08: 22,344 matches found across a full
    sweep). Do NOT use this for `settled` — a 30k-market sample turned up zero
    matches, because settled history is even more dominated by high-frequency
    esports/sports resolutions than the open set; use backfill_settled_markets
    (per-series query) for that instead.

    Stops adding *new* tickers once max_db_size_bytes is exceeded, but still
    snapshots already-tracked ones for this cycle."""
    tickers: list[str] = []
    cursor: str | None = None
    skipped_new_over_cap = 0

    while True:
        params: dict[str, object] = {"status": status, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = await client.get_markets(**params)
        markets = page.get("markets", [])

        size_ok = _db_size_bytes(database_url) < _effective_size_cap(max_db_size_bytes)

        for m in markets:
            if series_ticker_from_event(m.get("event_ticker", "")) not in allowed_series:
                continue

            ticker = m["ticker"]
            is_new = session.get(Market, ticker) is None
            if is_new and not size_ok:
                skipped_new_over_cap += 1
                continue

            tickers.append(_store_market_and_snapshot(session, m))

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


async def backfill_settled_markets(
    client: KalshiClient,
    session: Session,
    *,
    allowed_series: set[str],
    max_db_size_bytes: int,
    database_url: str,
) -> list[str]:
    """Resolved-outcome data for the favorite-longshot calibration curve (S2)
    — one /markets?series_ticker=X&status=settled query per allowed series,
    since a broad settled-status sweep doesn't reach the target categories
    within any reasonable page budget (see sync_markets_and_snapshot docstring).
    ~5k series at the read-rate limit takes a few minutes, not hours.

    Size cap is checked before every page, not just before every series — a
    single series can have many thousands of settled markets across many
    pages, and checking only between series let one series' backfill blow
    past the cap by 12MB+ before the next check could catch it (hit this for
    real 2026-07-08)."""
    tickers: list[str] = []
    for series_ticker in allowed_series:
        cursor: str | None = None
        while True:
            if _db_size_bytes(database_url) >= _effective_size_cap(max_db_size_bytes):
                logger.warning(
                    "DB at/over %.0f MB cap — stopping settled backfill early (mid-series %s)",
                    max_db_size_bytes / 1_000_000, series_ticker,
                )
                return tickers

            params: dict[str, object] = {
                "series_ticker": series_ticker,
                "status": "settled",
                "mve_filter": "exclude",
                "limit": 1000,
            }
            if cursor:
                params["cursor"] = cursor
            page = await client.get_markets(**params)
            markets = page.get("markets", [])
            for m in markets:
                tickers.append(_store_market_and_snapshot(session, m))
            session.commit()
            cursor = page.get("cursor")
            if not cursor or not markets:
                break

    logger.info("settled backfill: stored %d resolved markets across %d series", len(tickers), len(allowed_series))
    return tickers


async def run_poller(settings: Settings) -> None:
    """Continuous data-refresh loop (this is what the Windows Scheduled Task
    launches on network reconnect, and what the orchestrator's data step
    uses). Uses sync_latest_prices (bounded, upserted) rather than
    sync_markets_and_snapshot (append-only, meant for one-time/manual
    historical-corpus building) — a repeating loop calling the append-only
    version would blow the 500MB cap within days regardless of the cap
    check, since that check only gates *new* markets, not repeated
    snapshots of already-tracked ones."""
    engine = make_engine(settings)
    init_db(engine)
    session_factory = make_session_factory(engine)

    async with KalshiClient(settings) as client:
        allowed_series = await load_allowed_series(client, settings.target_categories)
        while True:
            with session_factory() as session:
                tickers = await sync_latest_prices(client, session, allowed_series=allowed_series)
                logger.info(
                    "refreshed prices for %d markets in target categories, db=%.1f MB",
                    len(tickers),
                    _db_size_bytes(settings.database_url) / 1_000_000,
                )
            await asyncio.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from kalshi_agent.config import settings

    asyncio.run(run_poller(settings))
