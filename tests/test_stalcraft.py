import httpx
import pytest
import respx

from app.stalcraft import StalcraftClient


@pytest.mark.asyncio
async def test_sends_client_id_and_secret_headers():
    with respx.mock:
        route = respx.get("https://api.test/eu/emission").mock(
            return_value=httpx.Response(200, json={"currentStart": "2026-01-01T00:00:00Z"})
        )
        async with StalcraftClient("https://api.test", "42", "s3cret") as client:
            await client.get_emission("eu")

        request = route.calls.last.request
        assert request.headers["Client-Id"] == "42"
        assert request.headers["Client-Secret"] == "s3cret"


@pytest.mark.asyncio
async def test_parses_rate_limit_headers():
    with respx.mock:
        respx.get("https://api.test/eu/emission").mock(
            return_value=httpx.Response(
                200,
                json={},
                headers={
                    "X-RateLimit-Limit": "400",
                    "X-RateLimit-Remaining": "399",
                    "X-RateLimit-Reset": "1789290275687",
                },
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as client:
            _, rate = await client.get_emission("eu")

    assert rate.limit == 400
    assert rate.remaining == 399
    assert rate.reset_at == pytest.approx(1789290275.687)


@pytest.mark.asyncio
async def test_does_not_retry_on_404():
    with respx.mock:
        route = respx.get("https://api.test/eu/emission").mock(
            return_value=httpx.Response(404)
        )
        async with StalcraftClient("https://api.test", "1", "s") as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_emission("eu", retries=2)

        assert route.call_count == 1


@pytest.mark.asyncio
async def test_retries_on_500_then_succeeds():
    with respx.mock:
        route = respx.get("https://api.test/eu/emission").mock(
            side_effect=[httpx.Response(500), httpx.Response(200, json={})]
        )
        async with StalcraftClient("https://api.test", "1", "s") as client:
            response, _ = await client.get_emission("eu", retries=2)

        assert route.call_count == 2

    assert response == {}


@pytest.mark.asyncio
async def test_respects_retry_after_on_429():
    calls = {"n": 0}

    def _responder(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={})

    with respx.mock:
        respx.get("https://api.test/eu/emission").mock(side_effect=_responder)
        async with StalcraftClient("https://api.test", "1", "s") as client:
            response, _ = await client.get_emission("eu")

    assert response == {}
    assert calls["n"] == 2
