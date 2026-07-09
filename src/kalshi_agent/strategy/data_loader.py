from sqlalchemy import select
from sqlalchemy.orm import Session

from kalshi_agent.data.models import Market, PriceSnapshot


def load_resolved_observations(session: Session) -> list[tuple[float, bool]]:
    """(price, resolved_yes) pairs for calibration, one per resolved ticker —
    uses each ticker's *most recent* price snapshot. A ticker can have more
    than one snapshot if it was tracked while open and later picked up again
    by the settled backfill; taking the latest avoids treating a mid-trading
    price as if it were the settlement price."""
    resolved = dict(
        session.execute(select(Market.ticker, Market.result).where(Market.result.in_(("yes", "no")))).all()
    )
    if not resolved:
        return []

    rows = session.execute(
        select(PriceSnapshot.ticker, PriceSnapshot.last_price, PriceSnapshot.ts)
        .where(PriceSnapshot.ticker.in_(resolved.keys()))
        .order_by(PriceSnapshot.ticker, PriceSnapshot.ts.desc())
    ).all()

    latest_price: dict[str, float] = {}
    for ticker, last_price, _ts in rows:
        if ticker not in latest_price and last_price is not None:
            latest_price[ticker] = last_price

    observations: list[tuple[float, bool]] = []
    for ticker, result in resolved.items():
        price = latest_price.get(ticker)
        if price is None:
            continue
        observations.append((price, result == "yes"))
    return observations
