import datetime as dt
from dataclasses import dataclass

from kalshi_agent.strategy.calibration import CalibrationBucket, bucket_for_price
from kalshi_agent.strategy.external.weather import probability_exceeds_strike


@dataclass
class SignalResult:
    fair_value: float | None
    confidence: float  # 0..1, scales sizing per build spec §4 Signal interface


class FavoriteLongshotSignal:
    """S2 (build spec Phase 1, well-established): fade extreme longshots, lean
    into heavy favorites, using the realized-frequency-vs-price calibration
    curve. Returns no opinion (fair_value=None) below min_samples — an
    under-populated bucket is not evidence of anything."""

    def __init__(self, curve: list[CalibrationBucket], *, min_samples: int = 20, confidence_saturation: int = 200):
        self._curve = curve
        self._min_samples = min_samples
        self._confidence_saturation = confidence_saturation

    def evaluate(self, price: float) -> SignalResult:
        bucket = bucket_for_price(price, self._curve)
        if bucket is None or bucket.n < self._min_samples:
            return SignalResult(fair_value=None, confidence=0.0)
        confidence = min(1.0, bucket.n / self._confidence_saturation)
        return SignalResult(fair_value=bucket.realized_frequency, confidence=confidence)


@dataclass
class WeatherObservation:
    forecast_temp_f: float
    strike_temp_f: float
    target_date: dt.date
    forecast_made_at: dt.datetime


class WeatherSignal:
    """Real-forecast-driven fair-value estimator for Kalshi temperature-
    threshold markets (KXHIGH*/KXLOW*-style tickers), added 2026-07-09 at
    user request for real-world-data-informed trading. Unlike
    FavoriteLongshotSignal (pure historical price calibration), this uses an
    actual NWS point forecast as the fair-value input — genuinely new
    information the market price may not yet fully reflect, especially
    several days out.

    NOTE: the layer that would map a Kalshi ticker to a city/lat-lon/strike/
    date (so this signal can be wired into the main decision loop
    automatically) is NOT implemented yet — we have zero real weather-market
    examples in the local DB to verify a parser against (Climate and Weather
    wasn't in target_categories until this same change). This class itself is
    tested and usable standalone once you have a (forecast, strike, date)
    triple from any source; verify the ticker-parsing layer against real
    collected data before wiring this into automated decisions."""

    def __init__(self, *, base_stddev_f: float = 2.0, stddev_growth_per_day: float = 1.0) -> None:
        self._base_stddev_f = base_stddev_f
        self._stddev_growth_per_day = stddev_growth_per_day

    def evaluate(self, obs: WeatherObservation) -> SignalResult:
        days_ahead = (obs.target_date - obs.forecast_made_at.date()).days
        p = probability_exceeds_strike(
            forecast_temp_f=obs.forecast_temp_f,
            strike_temp_f=obs.strike_temp_f,
            days_ahead=days_ahead,
            base_stddev_f=self._base_stddev_f,
            stddev_growth_per_day=self._stddev_growth_per_day,
        )
        # Confidence decays with lead time — a same-day forecast is far more
        # trustworthy than one six days out.
        confidence = max(0.1, 1.0 - 0.12 * max(0, days_ahead))
        return SignalResult(fair_value=p, confidence=confidence)
