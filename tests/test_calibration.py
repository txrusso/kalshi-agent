from kalshi_agent.strategy.calibration import build_calibration_curve, bucket_for_price
from kalshi_agent.strategy.signals import FavoriteLongshotSignal


def test_build_calibration_curve_buckets_and_frequency():
    # 10 observations at price ~0.10, only 2 resolve yes -> realized_frequency 0.2
    obs = [(0.10, True), (0.10, False), (0.10, False), (0.10, False), (0.10, False),
           (0.12, False), (0.12, False), (0.12, False), (0.12, False), (0.12, True)]
    curve = build_calibration_curve(obs, bucket_width=0.05)

    bucket = bucket_for_price(0.10, curve)
    assert bucket is not None
    assert bucket.n == 10
    assert bucket.n_yes == 2
    assert bucket.realized_frequency == 0.2


def test_bucket_for_price_boundaries():
    curve = build_calibration_curve([], bucket_width=0.05)
    assert bucket_for_price(0.0, curve).price_low == 0.0
    assert bucket_for_price(0.999, curve).price_high == 1.0
    assert bucket_for_price(1.0, curve).price_high == 1.0  # top edge falls into last bucket


def test_favorite_longshot_bias_shows_up_as_edge():
    # Simulate the documented bias: 10c longshots winning far less than 10% of the time.
    obs = [(0.10, False)] * 95 + [(0.10, True)] * 5
    curve = build_calibration_curve(obs, bucket_width=0.05)
    signal = FavoriteLongshotSignal(curve, min_samples=20)

    result = signal.evaluate(0.10)
    assert result.fair_value is not None
    assert result.fair_value < 0.10  # realized win rate (5%) well below the 10c price


def test_favorite_longshot_signal_no_opinion_below_min_samples():
    obs = [(0.10, True), (0.10, False)]  # only 2 observations
    curve = build_calibration_curve(obs, bucket_width=0.05)
    signal = FavoriteLongshotSignal(curve, min_samples=20)

    result = signal.evaluate(0.10)
    assert result.fair_value is None
    assert result.confidence == 0.0


def test_favorite_longshot_signal_confidence_scales_with_sample_size():
    obs_small = [(0.50, i % 2 == 0) for i in range(40)]
    obs_large = [(0.50, i % 2 == 0) for i in range(400)]

    small_curve = build_calibration_curve(obs_small, bucket_width=0.05)
    large_curve = build_calibration_curve(obs_large, bucket_width=0.05)

    signal_small = FavoriteLongshotSignal(small_curve, min_samples=20, confidence_saturation=200)
    signal_large = FavoriteLongshotSignal(large_curve, min_samples=20, confidence_saturation=200)

    assert signal_small.evaluate(0.50).confidence < signal_large.evaluate(0.50).confidence
    assert signal_large.evaluate(0.50).confidence == 1.0  # saturated
