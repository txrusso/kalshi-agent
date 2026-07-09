import pytest

from kalshi_agent.risk.fees import maker_fee, taker_fee


def test_taker_fee_max_at_50_cents():
    # 0.07 * 0.5 * 0.5 = 0.0175 -> rounds up to 2c per contract
    assert taker_fee(0.50, 1) == 0.02


def test_taker_fee_symmetric_around_50_cents():
    assert taker_fee(0.30, 100) == taker_fee(0.70, 100)


def test_taker_fee_smaller_at_extremes():
    assert taker_fee(0.05, 100) < taker_fee(0.50, 100)
    assert taker_fee(0.95, 100) < taker_fee(0.50, 100)


def test_maker_fee_is_quarter_of_taker_fee_pre_rounding():
    # Compare at a scale where rounding noise is negligible.
    t = taker_fee(0.50, 10_000)
    m = maker_fee(0.50, 10_000)
    assert m == pytest.approx(t / 4, rel=0.01)


def test_rejects_price_outside_open_unit_interval():
    with pytest.raises(ValueError):
        taker_fee(0.0, 1)
    with pytest.raises(ValueError):
        taker_fee(1.0, 1)
