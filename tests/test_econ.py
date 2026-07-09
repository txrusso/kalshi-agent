import httpx

from kalshi_agent.strategy.external.econ import FREDClient


async def test_latest_observation_parses_numeric_value():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["series_id"] == "UNRATE"
        assert request.url.params["api_key"] == "test-key"
        return httpx.Response(
            200,
            json={"observations": [{"date": "2026-06-01", "value": "4.10"}]},
        )

    client = FREDClient("test-key", transport=httpx.MockTransport(handler))
    try:
        obs = await client.latest_observation("UNRATE")
    finally:
        await client.aclose()

    assert obs is not None
    assert obs.series_id == "UNRATE"
    assert obs.date == "2026-06-01"
    assert obs.value == 4.10


async def test_latest_observation_returns_none_for_missing_value():
    # FRED uses "." to represent a missing/not-yet-published observation.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"observations": [{"date": "2026-07-01", "value": "."}]})

    client = FREDClient("test-key", transport=httpx.MockTransport(handler))
    try:
        obs = await client.latest_observation("UNRATE")
    finally:
        await client.aclose()

    assert obs is None


async def test_latest_observation_returns_none_when_no_observations():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"observations": []})

    client = FREDClient("test-key", transport=httpx.MockTransport(handler))
    try:
        obs = await client.latest_observation("UNRATE")
    finally:
        await client.aclose()

    assert obs is None
