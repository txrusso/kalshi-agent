import datetime as dt
import math

import httpx

NWS_BASE = "https://api.weather.gov"


class NWSClient:
    """NOAA/National Weather Service point-forecast client. Free, keyless —
    NWS just requires an identifying User-Agent header. Verified live
    2026-07-09 against api.weather.gov/points and .../gridpoints/.../forecast."""

    def __init__(
        self,
        *,
        user_agent: str = "kalshi-agent (set a real contact before production use)",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=NWS_BASE, headers={"User-Agent": user_agent}, timeout=10.0, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "NWSClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def daily_forecast(self, lat: float, lon: float) -> list[dict]:
        """Raw list of forecast periods (day/night, ~7 days out) from NWS's
        two-step points -> gridpoints/forecast lookup."""
        points = await self._http.get(f"/points/{lat},{lon}")
        points.raise_for_status()
        forecast_url = points.json()["properties"]["forecast"]
        forecast = await self._http.get(forecast_url)
        forecast.raise_for_status()
        return forecast.json()["properties"]["periods"]


def forecast_high_for_date(periods: list[dict], target_date: dt.date) -> float | None:
    """The daytime period's forecast temperature (°F) for a given date. NWS
    periods alternate day/night; daytime ones have isDaytime=True."""
    for period in periods:
        start = dt.datetime.fromisoformat(period["startTime"]).date()
        if start == target_date and period.get("isDaytime"):
            return float(period["temperature"])
    return None


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def probability_exceeds_strike(
    *,
    forecast_temp_f: float,
    strike_temp_f: float,
    days_ahead: float,
    base_stddev_f: float = 2.0,
    stddev_growth_per_day: float = 1.0,
) -> float:
    """Documented heuristic, not a validated meteorological model: NWS's
    public API doesn't expose forecast uncertainty directly, so this
    approximates it as growing roughly linearly with lead time (same-day
    forecasts are typically accurate to ~1-2°F; multi-day ones have much more
    spread) and treats the outcome as normally distributed around the point
    forecast. Tune base_stddev_f / stddev_growth_per_day against realized
    forecast error for the specific station before trusting this with real
    capital — these defaults are reasonable starting guesses, not calibrated."""
    stddev = base_stddev_f + stddev_growth_per_day * max(0.0, days_ahead)
    z = (forecast_temp_f - strike_temp_f) / stddev
    return _normal_cdf(z)
