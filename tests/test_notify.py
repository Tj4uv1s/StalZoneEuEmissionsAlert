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


# --- format_market_digest ----------------------------------------------

from app.constants import MARKET_MIN_CHANGE_PCT, MARKET_MIN_LOTS  # noqa: E402
from app.notify import _format_compact, _telegram_len, format_market_digest  # noqa: E402

SOLID = MARKET_MIN_LOTS + 5  # лотів із запасом — пара точно не "тонка"
FOOTER = (
    f"Враховано пари, де лотів було від {MARKET_MIN_LOTS} в обидва дні, "
    f"і зміни від {MARKET_MIN_CHANGE_PCT:g}%."
)

DIGEST_NOW = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)  # 12:00 Києва
GUM = Artifact(id="gum", name="Жвачка", category="biochemical")
HEEL = Artifact(id="heel", name="Каблук", category="gravity")
DIGEST_CATALOG = (HEEL, GUM)


@pytest.mark.parametrize(
    "value,expected",
    [
        (950, "950"),
        (6_500, "6,5 тис"),
        (65_000, "65 тис"),
        (388_888, "389 тис"),
        (999_950, "1 млн"),  # не "1000 тис"
        (1_100_000, "1,1 млн"),
        (9_960_000, "10 млн"),  # не "10,0 млн"
        (47_000_000, "47 млн"),
        (1_234_000_000, "1,2 млрд"),
    ],
)
def test_compact_price(value, expected):
    assert _format_compact(value) == expected


def test_first_digest_is_price_list_sorted_by_name():
    current = {
        "gum:0": {"median": 20_000, "count": SOLID},
        "gum:5": {"median": 47_000_000, "count": SOLID},
        "heel:4": {"median": 2_500_000, "count": SOLID},
    }
    [text] = format_market_digest("EU", current, None, None, DIGEST_NOW, DIGEST_CATALOG)
    assert text == (
        "📊 Ринок артефактів EU · 28.09\n"
        "Перший зріз цін — динаміка з'явиться в наступному дайджесті.\n"
        "Медіанна ціна виставлених лотів за рідкістю:\n"
        "\n"
        "Жвачка — ⚪ 20 тис · 🟡 47 млн\n"
        "Каблук — 🔴 2,5 млн"
    )


def test_first_digest_on_empty_market():
    [text] = format_market_digest("EU", {}, None, None, DIGEST_NOW, DIGEST_CATALOG)
    assert text.endswith("На аукціоні зараз немає артефактів.")


def test_movers_digest_like_crypto_ticker():
    previous = {
        "gum:5": {"median": 40_000_000, "count": SOLID},
        "heel:4": {"median": 2_000_000, "count": SOLID},
    }
    current = {
        "gum:5": {"median": 47_000_000, "count": SOLID},
        "heel:4": {"median": 1_500_000, "count": SOLID},
    }
    [text] = format_market_digest(
        "EU", current, previous, "2026-09-27T09:00:00Z", DIGEST_NOW, DIGEST_CATALOG
    )
    assert text == (
        "📊 Ринок артефактів EU · 28.09\n"
        "Медіанна ціна виставлених лотів проти 27.09, 12:00\n"
        "\n"
        "🟢 Дорожчають\n"
        "🟢▲ +17,5%  🟡 Жвачка · Легендарный  40 млн → 47 млн\n"
        "\n"
        "🔴 Дешевшають\n"
        "🔴▼ −25,0%  🔴 Каблук · Исключительный  2 млн → 1,5 млн\n"
        "\n" + FOOTER
    )


def test_only_gainers_omits_losers_section():
    previous = {"gum:5": {"median": 100, "count": SOLID}}
    current = {"gum:5": {"median": 200, "count": SOLID}}
    [text] = format_market_digest("EU", current, previous, None, DIGEST_NOW, DIGEST_CATALOG)
    assert "🟢 Дорожчають" in text
    assert "🔴 Дешевшають" not in text


def test_no_significant_changes_message():
    previous = {"gum:5": {"median": 100, "count": 1}}  # тонкий ринок — не порівнюємо
    current = {"gum:5": {"median": 900, "count": 1}}
    [text] = format_market_digest(
        "EU", current, previous, "2026-09-27T09:00:00Z", DIGEST_NOW, DIGEST_CATALOG
    )
    assert text.endswith("Суттєвих змін цін немає.")


def test_unknown_artifact_id_falls_back_to_id():
    previous = {"ghost:2": {"median": 100, "count": SOLID}}
    current = {"ghost:2": {"median": 150, "count": SOLID}}
    [text] = format_market_digest("EU", current, previous, None, DIGEST_NOW, DIGEST_CATALOG)
    assert "🔵 ghost · Особый" in text


def test_big_price_list_is_split_under_telegram_limit():
    """103 артефакти × до 6 рідкостей не влазять в одне повідомлення."""
    catalog = tuple(Artifact(id=f"a{i}", name=f"Артефакт номер {i:03d}", category="gravity") for i in range(103))
    current = {
        f"a{i}:{q}": {"median": 1_234_567 * (q + 1), "count": 5}
        for i in range(103)
        for q in range(6)
    }
    messages = format_market_digest("EU", current, None, None, DIGEST_NOW, catalog)

    assert len(messages) > 1
    assert all(_telegram_len(m) <= 4096 for m in messages)
    # жоден артефакт не загубився й не розірвався між повідомленнями
    rows = [line for m in messages for line in m.splitlines() if line.startswith("Артефакт номер")]
    assert len(rows) == 103


def test_telegram_length_counts_emoji_as_two():
    assert _telegram_len("🟡") == 2
    assert _telegram_len("Ж") == 1


def test_thin_market_prices_are_marked_with_footnote():
    current = {
        "gum:3": {"median": 9_600_000, "count": 1},  # один лот — випадкова ціна
        "gum:4": {"median": 8_000_000, "count": 2},
        "heel:0": {"median": 20_000, "count": SOLID},
    }
    [text] = format_market_digest("EU", current, None, None, DIGEST_NOW, DIGEST_CATALOG)
    assert "Жвачка — 🟣 9,6 млн* · 🔴 8 млн*" in text
    assert "Каблук — ⚪ 20 тис\n" in text  # без зірочки
    assert text.endswith(f"* менше {MARKET_MIN_LOTS} лотів — ціна орієнтовна")


def test_no_footnote_when_all_prices_are_solid():
    current = {"heel:0": {"median": 20_000, "count": SOLID}}
    [text] = format_market_digest("EU", current, None, None, DIGEST_NOW, DIGEST_CATALOG)
    assert "*" not in text
