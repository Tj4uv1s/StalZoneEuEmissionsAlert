"""Тести форматування повідомлень: час у МСК, тривалості, кривi дані."""
import pytest

from app.notify import format_event
from app.tracker import ENDED, WARNING, Event


def test_warning_full_message():
    event = Event(WARNING, "EU", "2026-09-09T11:00:00Z", interval=5400, predicted_next="2026-09-09T11:05:00Z")
    assert format_event(event) == (
        "⚠️ Викид у регіоні EU!\n"
        "⏰ Почало: 14:00 МСК\n"
        "⏸️ З попереднього викиду: 1 г 30 хв\n"
        "📍 Орієнтовно закінчиться: 14:05 МСК"
    )


def test_ended_full_message():
    event = Event(ENDED, "RU", "2026-09-09T11:05:00Z", interval=300, predicted_next="2026-09-09T12:05:00Z")
    assert format_event(event) == (
        "✅ Викид у регіоні RU завершився.\n"
        "⏰ Закінчено: 14:05 МСК\n"
        "⌛ Тривав: 5 хв\n"
        "📍 Наступний орієнтовно: 15:05 МСК"
    )


def test_utc_is_converted_to_msk():
    # 21:30 UTC -> 00:30 МСК наступної доби
    event = Event(WARNING, "EU", "2026-09-09T21:30:00Z")
    assert "00:30 МСК" in format_event(event)


def test_offset_input_is_normalised_to_msk():
    # той самий момент, записаний у +03:00, має дати той самий рядок
    assert format_event(Event(WARNING, "EU", "2026-09-09T14:00:00+03:00")) == format_event(
        Event(WARNING, "EU", "2026-09-09T11:00:00Z")
    )


def test_optional_lines_are_omitted():
    event = Event(WARNING, "EU", "2026-09-09T11:00:00Z")
    assert format_event(event) == "⚠️ Викид у регіоні EU!\n⏰ Почало: 14:00 МСК"


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
    assert format_event(event) == "✅ Викид у регіоні EU завершився.\n⏰ Закінчено: 14:00 МСК"


def test_unknown_kind_falls_back_to_plain_line():
    assert format_event(Event("whatever", "EU", "2026-09-09T11:00:00Z")) == (
        "EU: whatever 2026-09-09T11:00:00Z"
    )
