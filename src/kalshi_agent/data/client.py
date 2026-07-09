import asyncio
import logging
from typing import Any

import httpx

from kalshi_agent.config import Settings
from kalshi_agent.data.auth import API_PREFIX, KalshiSigner
from kalshi_agent.data.ratelimit import TokenBucket

logger = logging.getLogger(__name__)

MAX_RETRIES = 5


class KalshiClient:
    """Async REST client for Kalshi's trade API v2. Handles RSA-PSS request
    signing, client-side rate limiting, and retry/backoff on 429/5xx."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._signer = KalshiSigner(settings.kalshi_api_key_id, settings.private_key_pem)
        self._http = httpx.AsyncClient(base_url=settings.kalshi_rest_base, timeout=10.0, transport=transport)
        self._read_bucket = TokenBucket(rate=settings.read_rate_limit, capacity=settings.read_rate_limit)
        self._write_bucket = TokenBucket(rate=settings.write_rate_limit, capacity=settings.write_rate_limit)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "KalshiClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        authed: bool = True,
    ) -> Any:
        bucket = self._read_bucket if method == "GET" else self._write_bucket
        await bucket.acquire()

        headers = {}
        if authed:
            full_path = f"{API_PREFIX}{path}"
            headers = self._signer.headers(method, full_path)

        resp: httpx.Response | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as exc:
                # Connection-level failures (dropped connection, DNS hiccup,
                # timeout) — no status code to check, but still worth a retry.
                # Hit this for real via a mid-poll network drop 2026-07-08.
                backoff = min(2**attempt, 30)
                logger.warning(
                    "kalshi %s %s -> %s, retrying in %.0fs (attempt %d/%d)",
                    method, path, type(exc).__name__, backoff, attempt + 1, MAX_RETRIES,
                )
                if attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(backoff)
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                backoff = min(2**attempt, 30)
                logger.warning(
                    "kalshi %s %s -> %s, retrying in %.0fs (attempt %d/%d)",
                    method, path, resp.status_code, backoff, attempt + 1, MAX_RETRIES,
                )
                await asyncio.sleep(backoff)
                continue
            break

        assert resp is not None
        resp.raise_for_status()
        return resp.json()

    # -- Public discovery (no auth required) --------------------------------

    async def get_series(self, **params: Any) -> dict[str, Any]:
        return await self._request("GET", "/series", params=params, authed=False)

    async def get_events(self, **params: Any) -> dict[str, Any]:
        return await self._request("GET", "/events", params=params, authed=False)

    async def get_markets(self, **params: Any) -> dict[str, Any]:
        return await self._request("GET", "/markets", params=params, authed=False)

    async def get_market(self, ticker: str) -> dict[str, Any]:
        return await self._request("GET", f"/markets/{ticker}", authed=False)

    async def get_orderbook(self, ticker: str, depth: int | None = None) -> dict[str, Any]:
        params = {"depth": depth} if depth is not None else {}
        return await self._request("GET", f"/markets/{ticker}/orderbook", params=params, authed=False)

    # -- Authenticated portfolio -------------------------------------------

    async def get_balance(self) -> dict[str, Any]:
        return await self._request("GET", "/portfolio/balance")

    async def get_positions(self, **params: Any) -> dict[str, Any]:
        return await self._request("GET", "/positions", params=params)

    async def get_fills(self, **params: Any) -> dict[str, Any]:
        return await self._request("GET", "/portfolio/fills", params=params)

    async def get_orders(self, **params: Any) -> dict[str, Any]:
        return await self._request("GET", "/portfolio/events/orders", params=params)
