"""Тести спільних звітів (reports.py)."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app import reports
from app.auction import MarketFetch
from app.reports import MarketUnavailable, build_digest, last_emission_window
from app.stalcraft import StalcraftClient
from app.tracker import parse_iso

NOW = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)


async def _window(emission_json):
    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(200, json=emission_json)
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            return await last_emission_window(api, "EU", NOW, delay=1800)


@pytest.mark.asyncio
async def test_window_after_long_finished_emission_is_full_30_minutes():
    since, until = await _window({"previousEnd": "2026-09-28T14:00:00Z"})
    assert since == parse_iso("2026-09-28T14:00:00Z")
    assert until == parse_iso("2026-09-28T14:30:00Z")


@pytest.mark.asyncio
async def test_window_is_cut_at_now_if_30_minutes_not_passed_yet():
    since, until = await _window({"previousEnd": "2026-09-28T16:50:00Z"})
    assert since == parse_iso("2026-09-28T16:50:00Z")
    assert until == NOW


@pytest.mark.parametrize("payload", [{}, {"previousEnd": None}, {"previousEnd": "не час"}])
@pytest.mark.asyncio
async def test_no_window_without_usable_emission_end(payload):
    assert await _window(payload) is None


def _lots(*prices):
    return [{"buyoutPrice": p, "amount": 1, "additional": {"qlt": 2}} for p in prices]


@pytest.mark.asyncio
async def test_build_digest_returns_snapshot_and_messages(monkeypatch):
    async def fake_fetch(api, region, **kw):
        return MarketFetch(lots={"a1": _lots(100, 200, 300)}, failed=0, total=1)

    monkeypatch.setattr(reports, "fetch_market", fake_fetch)
    digest = await build_digest(None, "EU", [], NOW)

    assert digest.snapshot == {"a1:2": {"median": 200, "count": 3}}
    assert "Перший зріз цін" in digest.text


@pytest.mark.asyncio
async def test_build_digest_refuses_mostly_failed_market(monkeypatch):
    async def fake_fetch(api, region, **kw):
        return MarketFetch(lots={"a1": _lots(100)}, failed=60, total=103)

    monkeypatch.setattr(reports, "fetch_market", fake_fetch)
    with pytest.raises(MarketUnavailable, match="60 з 103"):
        await build_digest(None, "EU", [], NOW)


@pytest.mark.asyncio
async def test_build_digest_tolerates_minority_of_failures(monkeypatch):
    async def fake_fetch(api, region, **kw):
        return MarketFetch(lots={"a1": _lots(100)}, failed=51, total=103)  # рівно менше половини

    monkeypatch.setattr(reports, "fetch_market", fake_fetch)
    digest = await build_digest(None, "EU", [], NOW)
    assert digest.snapshot
