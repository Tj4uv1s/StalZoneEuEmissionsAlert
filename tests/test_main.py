"""Тести планування топу лотів аукціону в головному циклі (poll_once)."""
import dataclasses
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app import main
from app.storage import JsonStorage
from app.stalcraft import StalcraftClient
from app.tracker import parse_iso


class FakeNotifier:
    """Відправник, що просто запам'ятовує тексти замість мережі."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def broadcast(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def fast_config(monkeypatch):
    """Config із передбачуваними значеннями, без залежності від .env файлу."""
    test_config = dataclasses.replace(
        main.config,
        regions=["EU"],
        min_poll_interval=1.0,
        announce_max_age=900.0,
        top_lots_delay=1800.0,
    )
    monkeypatch.setattr(main, "config", test_config)
    return test_config


@pytest.fixture
def storage(tmp_path):
    return JsonStorage(tmp_path / "state.json")


NOW = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_ended_event_schedules_pending_top_lots(fast_config, storage):
    storage.set("EU", {"announced_current": "2026-09-26T09:46:00Z"})
    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(
                200,
                json={
                    "currentStart": None,
                    "previousStart": "2026-09-26T09:46:00Z",
                    "previousEnd": "2026-09-26T09:50:00Z",
                },
            )
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            await main.poll_once(api, storage, FakeNotifier(), now=NOW)

    pending = storage.get("EU")["pending_top_lots"]
    assert pending["since"] == "2026-09-26T09:50:00Z"
    assert parse_iso(pending["due"]) == parse_iso("2026-09-26T09:50:00Z") + timedelta(
        seconds=1800
    )


@pytest.mark.asyncio
async def test_top_lots_not_fetched_before_due_time(fast_config, storage):
    storage.set(
        "EU",
        {
            "announced_current": "2026-09-26T09:46:00Z",
            "announced_end": "2026-09-26T09:50:00Z",
            "pending_top_lots": {
                "since": "2026-09-26T09:50:00Z",
                "due": "2026-09-26T10:20:00Z",
            },
        },
    )
    notifier = FakeNotifier()
    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(200, json={"currentStart": None})
        )
        # лоти аукціону НЕ повинні запитуватись до due
        lots_route = respx.get(
            "https://api.test/EU/auction/grav1/lots"
        ).mock(return_value=httpx.Response(200, json={"total": 0, "lots": []}))
        async with StalcraftClient("https://api.test", "1", "s") as api:
            # NOW = 10:00, due = 10:20 — ще рано
            await main.poll_once(api, storage, notifier, now=NOW)

    assert lots_route.call_count == 0
    assert notifier.sent == []
    assert storage.get("EU")["pending_top_lots"]["due"] == "2026-09-26T10:20:00Z"


@pytest.mark.asyncio
async def test_top_lots_fetched_and_broadcast_when_due(fast_config, storage, monkeypatch):
    storage.set(
        "EU",
        {
            "announced_current": "2026-09-26T09:46:00Z",
            "announced_end": "2026-09-26T09:50:00Z",
            "pending_top_lots": {
                "since": "2026-09-26T09:50:00Z",
                "due": "2026-09-26T10:20:00Z",
            },
        },
    )
    notifier = FakeNotifier()
    windows = []

    async def fake_top_listings_between(api, region, since, until, **kwargs):
        assert region == "EU"
        windows.append((since, until))
        return ["не важливо, format_top_listings теж підмінимо"]

    monkeypatch.setattr(main, "top_listings_between", fake_top_listings_between)
    monkeypatch.setattr(main, "format_top_listings", lambda *a, **kw: "📦 топ лотів")

    # Перевіряємо із запізненням на 5 хв (пауза опитування, рестарт) —
    # вікно все одно має закінчуватись рівно на due, а не розповзатись до now.
    late_now = datetime(2026, 9, 26, 10, 25, 0, tzinfo=timezone.utc)
    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(200, json={"currentStart": None})
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            await main.poll_once(api, storage, notifier, now=late_now)

    assert windows == [
        (parse_iso("2026-09-26T09:50:00Z"), parse_iso("2026-09-26T10:20:00Z"))
    ]
    assert notifier.sent == ["📦 топ лотів"]
    assert "pending_top_lots" not in storage.get("EU")


@pytest.mark.asyncio
async def test_empty_listings_result_sends_nothing_but_clears_pending(
    fast_config, storage, monkeypatch
):
    storage.set(
        "EU",
        {
            "pending_top_lots": {
                "since": "2026-09-26T09:50:00Z",
                "due": "2026-09-26T10:20:00Z",
            }
        },
    )
    notifier = FakeNotifier()

    async def fake_top_listings_between(*a, **kw):
        return []

    monkeypatch.setattr(main, "top_listings_between", fake_top_listings_between)
    # format_top_listings([]) реально повертає None — не підміняємо, перевіряємо як є

    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(200, json={"currentStart": None})
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            await main.poll_once(
                api, storage, notifier, now=datetime(2026, 9, 26, 10, 20, 0, tzinfo=timezone.utc)
            )

    assert notifier.sent == []
    assert "pending_top_lots" not in storage.get("EU")


@pytest.mark.asyncio
async def test_broken_pending_state_is_dropped_without_crash(fast_config, storage):
    storage.set("EU", {"pending_top_lots": {"since": "не час", "due": "теж не час"}})
    notifier = FakeNotifier()
    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(200, json={"currentStart": None})
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            await main.poll_once(api, storage, notifier, now=NOW)

    assert notifier.sent == []
    assert "pending_top_lots" not in storage.get("EU")
