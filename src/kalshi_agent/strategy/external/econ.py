from dataclasses import dataclass

import httpx

FRED_BASE = "https://api.stlouisfed.org/fred"


@dataclass
class EconObservation:
    series_id: str
    date: str
    value: float


class FREDClient:
    """St. Louis Fed FRED API client for real economic indicator data
    (unemployment, GDP, CPI, etc). Requires a free API key — register at
    https://fred.stlouisfed.org/docs/api/api_key.html and set FRED_API_KEY in
    .env (see config.py's fred_api_key). Built against FRED's documented,
    stable REST format but NOT verified against a live key (none available
    in this environment, unlike NWS which is keyless) — confirm the response
    shape against a real key before relying on this.

    Common series IDs: UNRATE (unemployment rate), GDP (nominal GDP),
    CPIAUCSL (CPI, all urban consumers)."""

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._http = httpx.AsyncClient(base_url=FRED_BASE, timeout=10.0, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "FREDClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def latest_observation(self, series_id: str) -> EconObservation | None:
        """Most recent published value for a FRED series. Returns None if
        the series has no observations or the latest one isn't numeric (FRED
        uses "." for missing values)."""
        resp = await self._http.get(
            "/series/observations",
            params={
                "series_id": series_id,
                "api_key": self._api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
        )
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
        if not observations:
            return None
        latest = observations[0]
        try:
            value = float(latest["value"])
        except (KeyError, ValueError):
            return None
        return EconObservation(series_id=series_id, date=latest["date"], value=value)
