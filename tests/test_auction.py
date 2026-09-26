"""Тести збору статистики продажів артефактів."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.artifacts import Artifact
from app.auction import top_sales_since
from app.stalcraft import StalcraftClient

SINCE = datetime(2026, 9, 26, 9, 50, 0, tzinfo=timezone.utc)

GRAVITY = Artifact(id="grav1", name="Gravity Spring", category="gravity")
THERMAL = Artifact(id="therm1", name="Fireball", category="thermal")


def _price(time: str, price: int, amount: int = 1) -> dict:
    return {"amount": amount, "price": price, "time": time, "additional": {}}


@pytest.mark.asyncio
async def test_collects_sales_within_window():
    with respx.mock:
        respx.get("https://api.test/EU/auction/grav1/history").mock(
            return_value=httpx.Response(
                200,
                json={
                    "total": 1,
                    "prices": [_price("2026-09-26T10:00:00Z", 500000)],
                },
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            sales = await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY,))

    assert len(sales) == 1
    assert sales[0].price == 500000
    assert sales[0].artifact is GRAVITY


@pytest.mark.asyncio
async def test_stops_at_time_boundary_without_including_older_sale():
    with respx.mock:
        respx.get("https://api.test/EU/auction/grav1/history").mock(
            return_value=httpx.Response(
                200,
                json={
                    "total": 2,
                    "prices": [
                        _price("2026-09-26T10:00:00Z", 500000),  # свіжий
                        _price("2026-09-26T09:00:00Z", 999999),  # до вікна — не рахуємо
                    ],
                },
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            sales = await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY,))

    assert len(sales) == 1
    assert sales[0].price == 500000


@pytest.mark.asyncio
async def test_paginates_when_page_is_full_and_boundary_not_reached():
    with respx.mock:
        route = respx.get("https://api.test/EU/auction/grav1/history").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "total": 3,
                        "prices": [
                            _price("2026-09-26T10:02:00Z", 1),
                            _price("2026-09-26T10:01:00Z", 2),
                        ],
                    },
                ),
                httpx.Response(
                    200, json={"total": 3, "prices": [_price("2026-09-26T10:00:30Z", 3)]}
                ),
            ]
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            sales = await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY,), page_size=2)

    assert route.call_count == 2
    assert {s.price for s in sales} == {1, 2, 3}


@pytest.mark.asyncio
async def test_max_pages_caps_requests_per_artifact():
    with respx.mock:
        route = respx.get("https://api.test/EU/auction/grav1/history").mock(
            return_value=httpx.Response(
                200, json={"total": 100, "prices": [_price("2026-09-26T10:00:00Z", 1)]}
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY,), page_size=1, max_pages=2)

    assert route.call_count == 2


@pytest.mark.asyncio
async def test_broken_timestamp_stops_paging_without_crash():
    with respx.mock:
        respx.get("https://api.test/EU/auction/grav1/history").mock(
            return_value=httpx.Response(
                200,
                json={
                    "total": 2,
                    "prices": [
                        _price("не час", 500000),
                        _price("2026-09-26T10:00:00Z", 1),
                    ],
                },
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            sales = await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY,))

    assert sales == []  # зупинились на першому ж непридатному записі


@pytest.mark.asyncio
async def test_one_broken_artifact_does_not_stop_others():
    with respx.mock:
        respx.get("https://api.test/EU/auction/grav1/history").mock(
            return_value=httpx.Response(500)
        )
        respx.get("https://api.test/EU/auction/therm1/history").mock(
            return_value=httpx.Response(
                200, json={"total": 1, "prices": [_price("2026-09-26T10:00:00Z", 42)]}
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            sales = await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY, THERMAL))

    assert len(sales) == 1
    assert sales[0].artifact is THERMAL


@pytest.mark.asyncio
async def test_results_sorted_by_price_descending_and_truncated():
    with respx.mock:
        respx.get("https://api.test/EU/auction/grav1/history").mock(
            return_value=httpx.Response(
                200, json={"total": 1, "prices": [_price("2026-09-26T10:00:00Z", 100)]}
            )
        )
        respx.get("https://api.test/EU/auction/therm1/history").mock(
            return_value=httpx.Response(
                200, json={"total": 1, "prices": [_price("2026-09-26T10:00:00Z", 900)]}
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            sales = await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY, THERMAL), limit=1)

    assert len(sales) == 1
    assert sales[0].price == 900


@pytest.mark.asyncio
async def test_empty_history_produces_no_sales():
    with respx.mock:
        respx.get("https://api.test/EU/auction/grav1/history").mock(
            return_value=httpx.Response(200, json={"total": 0, "prices": []})
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            sales = await top_sales_since(api, "EU", SINCE, catalog=(GRAVITY,))

    assert sales == []
