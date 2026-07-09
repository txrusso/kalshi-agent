import datetime as dt
from typing import Literal


def classify_horizon(
    expiration_time: dt.datetime | None,
    *,
    short_term_horizon_days: float,
    now: dt.datetime | None = None,
) -> Literal["short", "long"]:
    """User directive 2026-07-09: focus more on shorter-term trades. The
    literature actually says the favorite-longshot bias is *largest* for
    long-dated contracts (build spec S3 / Page-Clemen 2013), so this isn't a
    hard cutoff — see decision.py, which just requires long-dated trades to
    clear a higher edge bar rather than blocking them outright. Unknown
    expiration is treated as long-term (conservative: needs the bigger edge)."""
    if expiration_time is None:
        return "long"
    now = now or dt.datetime.now(dt.timezone.utc)
    if expiration_time.tzinfo is None:
        expiration_time = expiration_time.replace(tzinfo=dt.timezone.utc)
    days_to_expiry = (expiration_time - now).total_seconds() / 86400
    return "short" if days_to_expiry <= short_term_horizon_days else "long"
