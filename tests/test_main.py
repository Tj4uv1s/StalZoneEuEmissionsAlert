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
        # 23:00 Києва — позаду часу всіх тестів топу лотів (NOW ~13:00 Києва),
        # щоб дайджест ринку в них не спрацьовував і не ліз в незамоканий API
        market_digest_hour=23,
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


# --- дайджест ринку ------------------------------------------------------

from app.auction import MarketFetch  # noqa: E402

DIGEST_TIME = datetime(2026, 9, 28, 9, 30, 0, tzinfo=timezone.utc)  # 12:30 Києва


@pytest.fixture
def digest_config(monkeypatch):
    test_config = dataclasses.replace(
        main.config, regions=["EU"], min_poll_interval=1.0, market_digest_hour=12
    )
    monkeypatch.setattr(main, "config", test_config)
    return test_config


def _lots(*prices, qlt=3):
    return [{"buyoutPrice": p, "amount": 1, "additional": {"qlt": qlt}} for p in prices]


async def _poll(storage, notifier, now):
    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(200, json={"currentStart": None})
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            await main.poll_once(api, storage, notifier, now=now)


@pytest.mark.asyncio
async def test_first_digest_saves_snapshot_and_sends_price_list(
    digest_config, storage, monkeypatch
):
    async def fake_fetch(api, region, **kw):
        return MarketFetch(lots={"a1": _lots(100, 200, 300)}, failed=0, total=1)

    monkeypatch.setattr(main, "fetch_market", fake_fetch)
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)

    market = storage.get("EU")["market"]
    assert market == {"taken_at": "2026-09-28T09:30:00Z", "prices": {"a1:3": {"median": 200, "count": 3}}}
    assert len(notifier.sent) == 1
    assert "Перший зріз цін" in notifier.sent[0]


@pytest.mark.asyncio
async def test_second_day_compares_with_previous_snapshot(digest_config, storage, monkeypatch):
    storage.set(
        "EU",
        {"market": {"taken_at": "2026-09-27T09:30:00Z", "prices": {"a1:3": {"median": 100, "count": 10}}}},
    )

    async def fake_fetch(api, region, **kw):
        return MarketFetch(lots={"a1": _lots(*[150] * 10)}, failed=0, total=1)

    monkeypatch.setattr(main, "fetch_market", fake_fetch)
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)

    assert "🟢▲ +50,0%" in notifier.sent[0]
    assert storage.get("EU")["market"]["prices"]["a1:3"]["median"] == 150


@pytest.mark.asyncio
async def test_digest_runs_once_per_day(digest_config, storage, monkeypatch):
    calls = []

    async def fake_fetch(api, region, **kw):
        calls.append(1)
        return MarketFetch(lots={"a1": _lots(100)}, failed=0, total=1)

    monkeypatch.setattr(main, "fetch_market", fake_fetch)
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)
    await _poll(storage, notifier, datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_mostly_failed_fetch_keeps_yesterday_and_schedules_retry(
    digest_config, storage, monkeypatch
):
    yesterday = {"taken_at": "2026-09-27T09:30:00Z", "prices": {"a1:3": {"median": 100, "count": 3}}}
    storage.set("EU", {"market": yesterday})

    async def fake_fetch(api, region, **kw):
        return MarketFetch(lots={}, failed=80, total=103)

    monkeypatch.setattr(main, "fetch_market", fake_fetch)
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)

    state = storage.get("EU")
    assert state["market"] == yesterday  # учорашні ціни не стерті
    assert state["market_retry_at"] == "2026-09-28T09:45:00Z"
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_telegram_outage_does_not_trigger_refetch_storm(digest_config, storage, monkeypatch):
    """Обрив мережі до Telegram не має змушувати обходити весь ринок щоопитування."""
    calls = []

    async def fake_fetch(api, region, **kw):
        calls.append(1)
        return MarketFetch(lots={"a1": _lots(100)}, failed=0, total=1)

    class BrokenNotifier(FakeNotifier):
        async def broadcast(self, text):
            raise httpx.ConnectError("telegram недоступний")

    monkeypatch.setattr(main, "fetch_market", fake_fetch)
    notifier = BrokenNotifier()
    await _poll(storage, notifier, DIGEST_TIME)
    await _poll(storage, notifier, DIGEST_TIME)

    assert len(calls) == 1
    assert "market" in storage.get("EU")
