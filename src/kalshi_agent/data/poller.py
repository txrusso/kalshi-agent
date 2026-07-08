import asyncio
import datetime as dt
import logging

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


async def sync_markets(client: KalshiClient, session: Session, *, status: str = "open") -> list[str]:
    """Upsert open-market metadata and return the list of tracked tickers."""
    tickers: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, object] = {"status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        page = await client.get_markets(**params)
        for m in page.get("markets", []):
            tickers.append(m["ticker"])
            session.merge(
                Market(
                    ticker=m["ticker"],
                    event_ticker=m.get("event_ticker", ""),
                    series_ticker=m.get("series_ticker", ""),
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
        session.commit()
        cursor = page.get("cursor")
        if not cursor:
            break
    return tickers


async def snapshot_prices(client: KalshiClient, session: Session, tickers: list[str]) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    for ticker in tickers:
        detail = await client.get_market(ticker)
        m = detail.get("market", {})
        session.add(
            PriceSnapshot(
                ticker=ticker,
                ts=now,
                yes_bid=m.get("yes_bid"),
                yes_ask=m.get("yes_ask"),
                last_price=m.get("last_price"),
                volume=m.get("volume"),
                open_interest=m.get("open_interest"),
            )
        )
    session.commit()


async def run_poller(settings: Settings) -> None:
    """Phase 0 MVP poller: syncs open-market metadata and takes a top-of-book
    price snapshot every `poll_interval_seconds`. One GET per market per cycle
    against the read-rate bucket — fine at current market counts, but revisit
    with the batched orderbook endpoint (build spec §3.1) if this gets slow."""
    engine = make_engine(settings)
    init_db(engine)
    session_factory = make_session_factory(engine)

    async with KalshiClient(settings) as client:
        while True:
            with session_factory() as session:
                tickers = await sync_markets(client, session)
                logger.info("tracking %d open markets", len(tickers))
                await snapshot_prices(client, session, tickers)
            await asyncio.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from kalshi_agent.config import settings

    asyncio.run(run_poller(settings))
