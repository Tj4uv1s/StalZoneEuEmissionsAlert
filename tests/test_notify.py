"""Тести форматування повідомлень: київський час, тривалості, кривi дані."""
from datetime import datetime, timezone

import pytest

from app.artifacts import Artifact
from app.auction import Sale
from app.notify import format_event, format_top_sales
from app.tracker import ENDED, WARNING, Event


def test_warning_full_message():
    event = Event(WARNING, "EU", "2026-09-09T11:00:00Z", interval=5400, predicted_next="2026-09-09T11:05:00Z")
    assert format_event(event) == (
        "⚠️ Викид у регіоні EU!\n"
        "⏰ Почало: 14:00 за Києвом\n"
        "⏸️ З попереднього викиду: 1 г 30 хв\n"
        "📍 Орієнтовно закінчиться: 14:05 за Києвом"
    )


def test_ended_full_message():
    event = Event(ENDED, "RU", "2026-09-09T11:05:00Z", interval=300, predicted_next="2026-09-09T12:05:00Z")
    assert format_event(event) == (
        "✅ Викид у регіоні RU завершився.\n"
        "⏰ Закінчено: 14:05 за Києвом\n"
        "⌛ Тривав: 5 хв\n"
        "📍 Наступний орієнтовно: 15:05 за Києвом"
    )


def test_summer_time_is_utc_plus_three():
    # EEST: 17:28 UTC -> 20:28 у Києві
    assert "20:28 за Києвом" in format_event(Event(WARNING, "EU", "2026-09-25T17:28:00Z"))


def test_winter_time_is_utc_plus_two():
    """Головна причина брати зону, а не офсет: узимку Київ — UTC+2, не +3."""
    assert "19:28 за Києвом" in format_event(Event(WARNING, "EU", "2026-12-25T17:28:00Z"))


def test_date_rolls_over_at_local_midnight():
    # 22:30 UTC влітку -> 01:30 наступної доби
    assert "01:30 за Києвом" in format_event(Event(WARNING, "EU", "2026-09-09T22:30:00Z"))


def test_offset_input_is_normalised():
    # той самий момент, записаний у +03:00, має дати той самий рядок
    assert format_event(Event(WARNING, "EU", "2026-09-09T14:00:00+03:00")) == format_event(
        Event(WARNING, "EU", "2026-09-09T11:00:00Z")
    )


def test_optional_lines_are_omitted():
    event = Event(WARNING, "EU", "2026-09-09T11:00:00Z")
    assert format_event(event) == "⚠️ Викид у регіоні EU!\n⏰ Почало: 14:00 за Києвом"


def test_zero_interval_is_still_shown():
    event = Event(ENDED, "EU", "2026-09-09T11:00:00Z", interval=0)
    assert "⌛ Тривав: 0 хв" in format_event(event)


@pytest.mark.parametrize("seconds,expected", [(59, "0 хв"), (60, "1 хв"), (3600, "1 г 0 хв"), (7860, "2 г 11 хв")])
def test_duration_formatting(seconds, expected):
    event = Event(ENDED, "EU", "2026-09-09T11:00:00Z", interval=seconds)
    assert f"⌛ Тривав: {expected}" in format_event(event)


def test_broken_time_does_not_crash_and_line_is_skipped():
    event = Event(WARNING, "EU", "2026-09-28T09:19:11+00:00+00:00", interval=300)
    text = format_event(event)
    assert text == "⚠️ Викид у регіоні EU!\n⏸️ З попереднього викиду: 5 хв"


def test_broken_prediction_line_is_skipped():
    event = Event(ENDED, "EU", "2026-09-09T11:00:00Z", predicted_next="не час")
    assert format_event(event) == "✅ Викид у регіоні EU завершився.\n⏰ Закінчено: 14:00 за Києвом"


def test_unknown_kind_falls_back_to_plain_line():
    assert format_event(Event("whatever", "EU", "2026-09-09T11:00:00Z")) == (
        "EU: whatever 2026-09-09T11:00:00Z"
    )


# --- format_top_sales -------------------------------------------------

SINCE = datetime(2026, 9, 26, 9, 50, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 9, 26, 10, 20, 0, tzinfo=timezone.utc)

GRAVITY = Artifact(id="g1", name="Gravity Spring", category="gravity")
THERMAL = Artifact(id="t1", name="Fireball", category="thermal")
UNKNOWN_CATEGORY = Artifact(id="u1", name="Mystery Thing", category="brand_new_category")


def test_no_sales_produces_no_message():
    assert format_top_sales("EU", [], SINCE, UNTIL) is None


def test_top_sales_message_format():
    sales = [Sale(GRAVITY, 1_500_000, 1, "2026-09-26T10:00:00Z")]
    text = format_top_sales("EU", sales, SINCE, UNTIL)
    assert text == (
        "📊 Топ-1 проданих артефактів у регіоні EU\n"
        "За 12:50 за Києвом — 13:20 за Києвом\n"
        "\n"
        "🥇 🌀 Gravity Spring — 1 500 000"
    )


def test_top_three_use_medals_then_numbers():
    sales = [
        Sale(GRAVITY, 500, 1, "2026-09-26T10:00:00Z"),
        Sale(THERMAL, 400, 1, "2026-09-26T10:00:00Z"),
        Sale(GRAVITY, 300, 1, "2026-09-26T10:00:00Z"),
        Sale(THERMAL, 200, 1, "2026-09-26T10:00:00Z"),
    ]
    text = format_top_sales("EU", sales, SINCE, UNTIL)
    lines = text.splitlines()[3:]
    assert [line.split(" ", 1)[0] for line in lines] == ["🥇", "🥈", "🥉", "4."]


def test_price_uses_space_thousands_separator():
    sales = [Sale(GRAVITY, 1234567, 1, "2026-09-26T10:00:00Z")]
    assert "1 234 567" in format_top_sales("EU", sales, SINCE, UNTIL)


def test_unknown_category_gets_fallback_emoji():
    sales = [Sale(UNKNOWN_CATEGORY, 100, 1, "2026-09-26T10:00:00Z")]
    assert "❔ Mystery Thing" in format_top_sales("EU", sales, SINCE, UNTIL)
