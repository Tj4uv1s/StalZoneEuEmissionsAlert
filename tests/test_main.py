"""Тести планування топу лотів аукціону в головному циклі (poll_once)."""
import dataclasses
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app import main, reports
from app.storage import JsonStorage
from app.stalcraft import StalcraftClient
from app.tracker import parse_iso


class FakeNotifier:
    """Відправник, що просто запам'ятовує тексти (і кнопки) замість мережі."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.buttons: list = []

    async def broadcast(self, text: str, button=None) -> None:
        self.sent.append(text)
        self.buttons.append(button)

    async def bot_username(self):
        return "TestBot"


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

    monkeypatch.setattr(reports, "top_listings_between", fake_top_listings_between)
    monkeypatch.setattr(reports, "format_top_listings", lambda *a, **kw: "📦 топ лотів")

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

    monkeypatch.setattr(reports, "top_listings_between", fake_top_listings_between)
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
from app.constants import MARKET_BASELINE_DAYS  # noqa: E402

DIGEST_TIME = datetime(2026, 9, 28, 17, 30, 0, tzinfo=timezone.utc)  # 20:30 Києва


@pytest.fixture
def digest_config(monkeypatch):
    test_config = dataclasses.replace(
        main.config, regions=["EU"], min_poll_interval=1.0, market_digest_hour=20
    )
    monkeypatch.setattr(main, "config", test_config)
    return test_config


def _lots(*prices, qlt=3):
    return [{"buyoutPrice": p, "amount": 1, "additional": {"qlt": qlt}} for p in prices]


def _fetch_returning(lots, failed=0, total=1, calls=None):
    async def fake_fetch(api, region, **kw):
        if calls is not None:
            calls.append(1)
        return MarketFetch(lots=lots, failed=failed, total=total)

    return fake_fetch


async def _poll(storage, notifier, now):
    with respx.mock:
        respx.get("https://api.test/EU/emission").mock(
            return_value=httpx.Response(200, json={"currentStart": None})
        )
        async with StalcraftClient("https://api.test", "1", "s") as api:
            await main.poll_once(api, storage, notifier, now=now)


@pytest.mark.asyncio
async def test_first_digest_starts_history_and_posts_with_bot_button(
    digest_config, storage, monkeypatch
):
    monkeypatch.setattr(reports, "fetch_market", _fetch_returning({"a1": _lots(100, 200, 300)}))
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)

    assert storage.get("EU")["market_history"] == [
        {"taken_at": "2026-09-28T17:30:00Z", "prices": {"a1:3": {"median": 200, "count": 3}}}
    ]
    assert "Перший зріз цін" in notifier.sent[0]
    assert notifier.buttons[0] == (
        "📊 Відкрити аналітику",
        "https://t.me/TestBot?start=market_EU",
    )


@pytest.mark.asyncio
async def test_old_single_snapshot_is_migrated_into_history(digest_config, storage, monkeypatch):
    """Оновлення бота не має губити зріз, знятий старою версією."""
    old = {"taken_at": "2026-09-27T17:30:00Z", "prices": {"a1:3": {"median": 100, "count": 10}}}
    storage.set("EU", {"market": old})
    monkeypatch.setattr(reports, "fetch_market", _fetch_returning({"a1": _lots(*[150] * 10)}))
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)

    state = storage.get("EU")
    assert "market" not in state
    assert [d["taken_at"] for d in state["market_history"]] == [
        "2026-09-27T17:30:00Z",
        "2026-09-28T17:30:00Z",
    ]
    assert "📤 Вигідно продати" in notifier.sent[0]  # +50% проти старого зрізу


@pytest.mark.asyncio
async def test_history_is_capped_at_baseline_plus_today(digest_config, storage, monkeypatch):
    history = [
        {"taken_at": f"2026-09-{d:02d}T17:30:00Z", "prices": {}} for d in range(10, 27)
    ]
    storage.set("EU", {"market_history": history})
    monkeypatch.setattr(reports, "fetch_market", _fetch_returning({"a1": _lots(100)}))
    await _poll(storage, FakeNotifier(), DIGEST_TIME)

    kept = storage.get("EU")["market_history"]
    assert len(kept) == MARKET_BASELINE_DAYS + 1
    assert kept[-1]["taken_at"] == "2026-09-28T17:30:00Z"


@pytest.mark.asyncio
async def test_digest_runs_once_per_day(digest_config, storage, monkeypatch):
    calls = []
    monkeypatch.setattr(reports, "fetch_market", _fetch_returning({"a1": _lots(100)}, calls=calls))
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)
    await _poll(storage, notifier, datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_digest_waits_for_20_kyiv(digest_config, storage, monkeypatch):
    calls = []
    monkeypatch.setattr(reports, "fetch_market", _fetch_returning({"a1": _lots(100)}, calls=calls))
    # 16:59 UTC = 19:59 Києва
    await _poll(storage, FakeNotifier(), datetime(2026, 9, 28, 16, 59, tzinfo=timezone.utc))
    assert calls == []


@pytest.mark.asyncio
async def test_mostly_failed_fetch_keeps_history_and_schedules_retry(
    digest_config, storage, monkeypatch
):
    history = [{"taken_at": "2026-09-27T17:30:00Z", "prices": {"a1:3": {"median": 100, "count": 9}}}]
    storage.set("EU", {"market_history": history})
    monkeypatch.setattr(reports, "fetch_market", _fetch_returning({}, failed=80, total=103))
    notifier = FakeNotifier()
    await _poll(storage, notifier, DIGEST_TIME)

    state = storage.get("EU")
    assert state["market_history"] == history  # вчорашні ціни не стерті
    assert state["market_retry_at"] == "2026-09-28T17:45:00Z"
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_telegram_outage_does_not_trigger_refetch_storm(digest_config, storage, monkeypatch):
    """Обрив мережі до Telegram не має змушувати обходити весь ринок щоопитування."""
    calls = []
    monkeypatch.setattr(reports, "fetch_market", _fetch_returning({"a1": _lots(100)}, calls=calls))

    class BrokenNotifier(FakeNotifier):
        async def broadcast(self, text, button=None):
            raise httpx.ConnectError("telegram недоступний")

    notifier = BrokenNotifier()
    await _poll(storage, notifier, DIGEST_TIME)
    await _poll(storage, notifier, DIGEST_TIME)

    assert len(calls) == 1
    assert storage.get("EU")["market_history"]
