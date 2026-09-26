"""Тести збору топу виставлених на аукціон артефактів."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.artifacts import Artifact
from app.auction import top_listings_between
from app.stalcraft import StalcraftClient

SINCE = datetime(2026, 9, 26, 9, 50, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 9, 26, 10, 20, 0, tzinfo=timezone.utc)

GRAVITY = Artifact(id="grav1", name="Пружина", category="gravity")
THERMAL = Artifact(id="therm1", name="Огненный шар", category="thermal")

GRAV_URL = "https://api.test/EU/auction/grav1/lots"
THERM_URL = "https://api.test/EU/auction/therm1/lots"


def _lot(start: str, buyout: int, qlt=3, **extra) -> dict:
    lot = {
        "itemId": "x",
        "amount": 1,
        "startPrice": 0,
        "buyoutPrice": buyout,
        "startTime": start,
        "endTime": "2026-09-27T00:00:00Z",
        "additional": {} if qlt is None else {"qlt": qlt},
    }
    lot.update(extra)
    return lot


def _page(*lots) -> httpx.Response:
    return httpx.Response(200, json={"total": len(lots), "lots": list(lots)})


async def _run(**kwargs):
    async with StalcraftClient("https://api.test", "1", "s") as api:
        return await top_listings_between(api, "EU", SINCE, UNTIL, **kwargs)


@pytest.mark.asyncio
async def test_collects_listing_with_quality():
    with respx.mock:
        respx.get(GRAV_URL).mock(return_value=_page(_lot("2026-09-26T10:00:00Z", 500000, qlt=5)))
        listings = await _run(catalog=(GRAVITY,))

    assert len(listings) == 1
    assert listings[0].artifact is GRAVITY
    assert listings[0].price == 500000
    assert listings[0].quality == 5
    assert listings[0].listed_at == "2026-09-26T10:00:00Z"


@pytest.mark.asyncio
async def test_requests_newest_first_with_additional_info():
    """Без sort=time_created desc зупинка на межі вікна була б хибною,
    без additional=true не було б рідкості."""
    with respx.mock:
        route = respx.get(GRAV_URL).mock(return_value=_page())
        await _run(catalog=(GRAVITY,))

    params = route.calls.last.request.url.params
    assert params["sort"] == "time_created"
    assert params["order"] == "desc"
    assert params["additional"] == "true"


@pytest.mark.asyncio
async def test_lots_listed_before_emission_end_are_excluded():
    with respx.mock:
        respx.get(GRAV_URL).mock(
            return_value=_page(
                _lot("2026-09-26T10:00:00Z", 500000),
                _lot("2026-09-26T09:00:00Z", 999999),  # до кінця викиду — не рахуємо
            )
        )
        listings = await _run(catalog=(GRAVITY,))

    assert [listing.price for listing in listings] == [500000]


@pytest.mark.asyncio
async def test_lots_listed_after_window_are_skipped_but_scan_continues():
    with respx.mock:
        respx.get(GRAV_URL).mock(
            return_value=_page(
                _lot("2026-09-26T10:30:00Z", 999999),  # після вікна — пропускаємо
                _lot("2026-09-26T10:05:00Z", 100),     # у вікні — беремо
            )
        )
        listings = await _run(catalog=(GRAVITY,))

    assert [listing.price for listing in listings] == [100]


@pytest.mark.asyncio
async def test_window_boundaries_are_inclusive():
    with respx.mock:
        respx.get(GRAV_URL).mock(
            return_value=_page(
                _lot("2026-09-26T10:20:00Z", 2),  # рівно until
                _lot("2026-09-26T09:50:00Z", 1),  # рівно since
            )
        )
        listings = await _run(catalog=(GRAVITY,))

    assert sorted(listing.price for listing in listings) == [1, 2]


@pytest.mark.asyncio
async def test_paginates_when_page_is_full_and_boundary_not_reached():
    with respx.mock:
        route = respx.get(GRAV_URL).mock(
            side_effect=[
                _page(_lot("2026-09-26T10:02:00Z", 1), _lot("2026-09-26T10:01:00Z", 2)),
                _page(_lot("2026-09-26T10:00:30Z", 3)),
            ]
        )
        listings = await _run(catalog=(GRAVITY,), page_size=2)

    assert route.call_count == 2
    assert route.calls[1].request.url.params["offset"] == "2"
    assert {listing.price for listing in listings} == {1, 2, 3}


@pytest.mark.asyncio
async def test_max_pages_caps_requests_per_artifact():
    with respx.mock:
        route = respx.get(GRAV_URL).mock(return_value=_page(_lot("2026-09-26T10:00:00Z", 1)))
        await _run(catalog=(GRAVITY,), page_size=1, max_pages=2)

    assert route.call_count == 2


@pytest.mark.asyncio
async def test_broken_start_time_stops_scan_without_crash():
    with respx.mock:
        respx.get(GRAV_URL).mock(
            return_value=_page(_lot("не час", 500000), _lot("2026-09-26T10:00:00Z", 1))
        )
        listings = await _run(catalog=(GRAVITY,))

    assert listings == []  # зупинились на першому ж непридатному записі


@pytest.mark.parametrize("additional", [{}, {"qlt": "5"}, {"qlt": True}, None])
@pytest.mark.asyncio
async def test_missing_or_bad_quality_becomes_none(additional):
    lot = _lot("2026-09-26T10:00:00Z", 100)
    lot["additional"] = additional
    with respx.mock:
        respx.get(GRAV_URL).mock(return_value=_page(lot))
        listings = await _run(catalog=(GRAVITY,))

    assert listings[0].quality is None


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"buyoutPrice": 700}, 700),
        ({"buyoutPrice": 0, "currentPrice": 300, "startPrice": 100}, 300),  # лише ставка
        ({"buyoutPrice": 0, "startPrice": 100}, 100),
        ({"buyoutPrice": 0, "startPrice": 0}, 0),
    ],
)
@pytest.mark.asyncio
async def test_price_falls_back_from_buyout_to_bid(fields, expected):
    lot = _lot("2026-09-26T10:00:00Z", 0)
    lot.update(fields)
    with respx.mock:
        respx.get(GRAV_URL).mock(return_value=_page(lot))
        listings = await _run(catalog=(GRAVITY,))

    assert listings[0].price == expected


@pytest.mark.asyncio
async def test_one_broken_artifact_does_not_stop_others():
    with respx.mock:
        respx.get(GRAV_URL).mock(return_value=httpx.Response(500))
        respx.get(THERM_URL).mock(return_value=_page(_lot("2026-09-26T10:00:00Z", 42)))
        listings = await _run(catalog=(GRAVITY, THERMAL))

    assert len(listings) == 1
    assert listings[0].artifact is THERMAL


@pytest.mark.asyncio
async def test_results_sorted_by_price_descending_and_truncated():
    with respx.mock:
        respx.get(GRAV_URL).mock(return_value=_page(_lot("2026-09-26T10:00:00Z", 100)))
        respx.get(THERM_URL).mock(return_value=_page(_lot("2026-09-26T10:00:00Z", 900)))
        listings = await _run(catalog=(GRAVITY, THERMAL), limit=1)

    assert [listing.price for listing in listings] == [900]


@pytest.mark.asyncio
async def test_empty_lots_produce_nothing():
    with respx.mock:
        respx.get(GRAV_URL).mock(return_value=_page())
        listings = await _run(catalog=(GRAVITY,))

    assert listings == []


@pytest.mark.asyncio
async def test_concurrency_is_bounded():
    """Паралельно, але не всі 103 запити разом — інакше впремось у rate limit."""
    import asyncio

    in_flight = 0
    peak = 0

    async def _slow(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _page()

    catalog = tuple(Artifact(id=f"a{i}", name="Тест", category="gravity") for i in range(12))
    with respx.mock:
        respx.get(url__regex=r"https://api\.test/EU/auction/a\d+/lots").mock(side_effect=_slow)
        await _run(catalog=catalog, concurrency=3)

    assert peak == 3
