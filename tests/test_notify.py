"""Тести форматування повідомлень: київський час, тривалості, кривi дані."""
from datetime import datetime, timezone

import pytest

from app.artifacts import Artifact
from app.auction import Listing
from app.notify import format_event, format_top_listings
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


# --- format_top_listings ---------------------------------------------

SINCE = datetime(2026, 9, 26, 9, 50, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 9, 26, 10, 20, 0, tzinfo=timezone.utc)

SPRING = Artifact(id="g1", name="Пружина", category="gravity")
FIREBALL = Artifact(id="t1", name="Огненный шар", category="thermal")


def _listing(artifact, price, quality):
    return Listing(artifact, price, quality, "2026-09-26T10:00:00Z")


def test_nothing_listed_produces_no_message():
    assert format_top_listings("EU", [], SINCE, UNTIL) is None


def test_top_listings_message_format():
    text = format_top_listings("EU", [_listing(SPRING, 17_777_777, 5)], SINCE, UNTIL)
    assert text == (
        "📦 Топ-1 артефактів, виставлених на аукціон у регіоні EU\n"
        "🕒 12:50–13:20 за Києвом\n"
        "\n"
        "🥇 🟡 Пружина · Легендарный — 17 777 777"
    )


@pytest.mark.parametrize(
    "quality,expected",
    [
        (0, "⚪ Пружина · Обычный"),
        (1, "🟢 Пружина · Необычный"),
        (2, "🔵 Пружина · Особый"),
        (3, "🟣 Пружина · Редкий"),
        (4, "🔴 Пружина · Исключительный"),
        (5, "🟡 Пружина · Легендарный"),
    ],
)
def test_every_quality_has_color_and_russian_name(quality, expected):
    text = format_top_listings("EU", [_listing(SPRING, 100, quality)], SINCE, UNTIL)
    assert expected in text


@pytest.mark.parametrize("quality", [None, 6, -1])
def test_unknown_quality_shows_name_without_label(quality):
    text = format_top_listings("EU", [_listing(SPRING, 100, quality)], SINCE, UNTIL)
    assert text.splitlines()[-1] == "🥇 ❔ Пружина — 100"


def test_top_three_use_medals_then_numbers():
    listings = [_listing(SPRING, price, 0) for price in (500, 400, 300, 200)]
    lines = format_top_listings("EU", listings, SINCE, UNTIL).splitlines()[3:]
    assert [line.split(" ", 1)[0] for line in lines] == ["🥇", "🥈", "🥉", "4."]


def test_full_top_ten_has_ten_rows():
    listings = [_listing(FIREBALL, 1000 - i, i % 6) for i in range(10)]
    text = format_top_listings("EU", listings, SINCE, UNTIL)
    assert text.startswith("📦 Топ-10 ")
    assert len(text.splitlines()) == 3 + 10
    assert text.splitlines()[-1].startswith("10. ")


def test_price_uses_space_thousands_separator():
    text = format_top_listings("EU", [_listing(SPRING, 1234567, 0)], SINCE, UNTIL)
    assert "1 234 567" in text


def test_window_is_shown_in_kyiv_time_across_dst():
    # узимку Київ UTC+2: 09:50Z -> 11:50
    winter_since = datetime(2026, 12, 26, 9, 50, tzinfo=timezone.utc)
    winter_until = datetime(2026, 12, 26, 10, 20, tzinfo=timezone.utc)
    text = format_top_listings("EU", [_listing(SPRING, 1, 0)], winter_since, winter_until)
    assert "🕒 11:50–12:20 за Києвом" in text
