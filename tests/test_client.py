import httpx

from kalshi_agent.config import Settings
from kalshi_agent.data.client import KalshiClient


def _settings() -> Settings:
    return Settings(read_rate_limit=1000, write_rate_limit=1000)


async def test_retries_on_transport_error_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("simulated dropped connection", request=request)
        return httpx.Response(200, json={"markets": []})

    transport = httpx.MockTransport(handler)
    client = KalshiClient(_settings(), transport=transport)
    try:
        result = await client.get_markets()
    finally:
        await client.aclose()

    assert result == {"markets": []}
    assert calls["n"] == 2


async def test_retries_on_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"markets": []})

    transport = httpx.MockTransport(handler)
    client = KalshiClient(_settings(), transport=transport)
    try:
        result = await client.get_markets()
    finally:
        await client.aclose()

    assert result == {"markets": []}
    assert calls["n"] == 2
