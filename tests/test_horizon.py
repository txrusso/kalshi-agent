import datetime as dt

from kalshi_agent.strategy.horizon import classify_horizon


def test_short_term_within_window():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    expiry = now + dt.timedelta(days=3)
    assert classify_horizon(expiry, short_term_horizon_days=7.0, now=now) == "short"


def test_long_term_beyond_window():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    expiry = now + dt.timedelta(days=30)
    assert classify_horizon(expiry, short_term_horizon_days=7.0, now=now) == "long"


def test_exactly_at_boundary_is_short():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    expiry = now + dt.timedelta(days=7)
    assert classify_horizon(expiry, short_term_horizon_days=7.0, now=now) == "short"


def test_unknown_expiration_treated_as_long():
    assert classify_horizon(None, short_term_horizon_days=7.0) == "long"


def test_naive_datetime_assumed_utc():
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    naive_expiry = (now + dt.timedelta(days=2)).replace(tzinfo=None)
    assert classify_horizon(naive_expiry, short_term_horizon_days=7.0, now=now) == "short"
