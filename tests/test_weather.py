import datetime as dt

import httpx
import pytest

from kalshi_agent.strategy.external.weather import NWSClient, forecast_high_for_date, probability_exceeds_strike
from kalshi_agent.strategy.signals import WeatherObservation, WeatherSignal


def test_probability_exceeds_strike_high_when_forecast_well_above():
    p = probability_exceeds_strike(forecast_temp_f=90, strike_temp_f=70, days_ahead=0)
    assert p > 0.99


def test_probability_exceeds_strike_low_when_forecast_well_below():
    p = probability_exceeds_strike(forecast_temp_f=50, strike_temp_f=70, days_ahead=0)
    assert p < 0.01


def test_probability_exceeds_strike_near_half_at_the_strike():
    p = probability_exceeds_strike(forecast_temp_f=70, strike_temp_f=70, days_ahead=0)
    assert p == pytest.approx(0.5, abs=0.01)


def test_probability_uncertainty_grows_with_lead_time():
    # Forecast is 3F above strike -- more lead time (more uncertainty) should
    # push the probability of exceeding it closer to 50% (less confident).
    near = probability_exceeds_strike(forecast_temp_f=73, strike_temp_f=70, days_ahead=0)
    far = probability_exceeds_strike(forecast_temp_f=73, strike_temp_f=70, days_ahead=6)
    assert far < near
    assert far > 0.5  # still leans the same direction, just less confidently


def test_forecast_high_for_date_picks_daytime_period():
    periods = [
        {"name": "Tonight", "startTime": "2026-07-09T18:00:00-04:00", "isDaytime": False, "temperature": 72},
        {"name": "Friday", "startTime": "2026-07-10T06:00:00-04:00", "isDaytime": True, "temperature": 87},
        {"name": "Friday Night", "startTime": "2026-07-10T18:00:00-04:00", "isDaytime": False, "temperature": 73},
    ]
    result = forecast_high_for_date(periods, dt.date(2026, 7, 10))
    assert result == 87.0


def test_forecast_high_for_date_returns_none_when_not_found():
    periods = [{"name": "Today", "startTime": "2026-07-09T08:00:00-04:00", "isDaytime": True, "temperature": 82}]
    assert forecast_high_for_date(periods, dt.date(2026, 7, 20)) is None


async def test_nws_client_two_step_lookup_with_mock_transport():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/points/" in str(request.url):
            return httpx.Response(200, json={"properties": {"forecast": "https://api.weather.gov/gridpoints/OKX/33,42/forecast"}})
        return httpx.Response(200, json={"properties": {"periods": [{"name": "Today", "temperature": 82, "isDaytime": True, "startTime": "2026-07-09T08:00:00-04:00"}]}})

    transport = httpx.MockTransport(handler)
    client = NWSClient(transport=transport)
    try:
        periods = await client.daily_forecast(40.7128, -74.0060)
    finally:
        await client.aclose()

    assert len(calls) == 2
    assert periods[0]["temperature"] == 82


def test_weather_signal_evaluate_returns_fair_value_and_confidence():
    signal = WeatherSignal()
    obs = WeatherObservation(
        forecast_temp_f=90,
        strike_temp_f=70,
        target_date=dt.date(2026, 7, 9),
        forecast_made_at=dt.datetime(2026, 7, 9, 8, tzinfo=dt.timezone.utc),
    )
    result = signal.evaluate(obs)
    assert result.fair_value is not None
    assert result.fair_value > 0.99
    assert result.confidence == pytest.approx(1.0, abs=0.01)  # same-day, max confidence


def test_weather_signal_confidence_decays_with_lead_time():
    signal = WeatherSignal()
    made_at = dt.datetime(2026, 7, 9, 8, tzinfo=dt.timezone.utc)

    near = signal.evaluate(WeatherObservation(forecast_temp_f=75, strike_temp_f=70, target_date=dt.date(2026, 7, 9), forecast_made_at=made_at))
    far = signal.evaluate(WeatherObservation(forecast_temp_f=75, strike_temp_f=70, target_date=dt.date(2026, 7, 15), forecast_made_at=made_at))

    assert far.confidence < near.confidence
