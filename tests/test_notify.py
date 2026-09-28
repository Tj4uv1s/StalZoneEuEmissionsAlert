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


# --- форматування ---------------------------------------------------------

from app.constants import MARKET_BASELINE_DAYS, MARKET_MIN_LOTS, MARKET_SIGNAL_PCT  # noqa: E402
from app.formatting import format_compact, format_pct, telegram_len, trend_arrow  # noqa: E402
from app.notify import format_market_digest, market_button  # noqa: E402


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
    assert format_compact(value) == expected


@pytest.mark.parametrize(
    "pct,expected",
    [(25.0, "+25%"), (-25.0, "−25%"), (-8.14, "−8,1%"), (0.4, "+0,4%"), (3.0, "+3%"), (156.7, "+157%")],
)
def test_percent_format(pct, expected):
    assert format_pct(pct) == expected


def test_trend_arrows_like_crypto():
    assert trend_arrow(5) == "🟢▲"
    assert trend_arrow(-5) == "🔴▼"


def test_telegram_length_counts_emoji_as_two():
    assert telegram_len("🟡") == 2
    assert telegram_len("Ж") == 1


# --- пост каналу: дайджест ринку ---------------------------------------------

DIGEST_NOW = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)  # 20:00 Києва
PRISM = Artifact(id="prism", name="Призма", category="other_arts")
GUM = Artifact(id="gum", name="Жвачка", category="biochemical")
HEEL = Artifact(id="heel", name="Каблук", category="gravity")
DIGEST_CATALOG = (PRISM, GUM, HEEL)
SOLID = MARKET_MIN_LOTS + 5


def snap(**prices):
    return {key: {"median": m, "count": c} for key, (m, c) in prices.items()}


def days(n, **prices):
    return [{"taken_at": f"2026-09-2{i}T17:00:00Z", "prices": snap(**prices)} for i in range(1, n + 1)]


def test_first_digest_explains_signals_come_tomorrow():
    text = format_market_digest("EU", snap(**{"gum:5": (47_000_000, SOLID)}), [], DIGEST_NOW, DIGEST_CATALOG)
    assert text.startswith("📊 Ринок артефактів EU · 28.09")
    assert "Перший зріз цін збережено" in text
    assert "💰" not in text


def test_buy_and_sell_signals_with_usual_price():
    history = days(7, **{"prism:2": (1_200_000, SOLID), "gum:5": (40_000_000, SOLID)})
    current = snap(**{"prism:2": (900_000, SOLID), "gum:5": (47_000_000, SOLID)})
    text = format_market_digest("EU", current, history, DIGEST_NOW, DIGEST_CATALOG)
    assert text == (
        "📊 Ринок артефактів EU · 28.09\n"
        "Звична ціна — середня за 7 дн.\n"
        "\n"
        "💰 Вигідно купити — дешевше, ніж зазвичай (1)\n"
        "🔵 Призма · Особый  −25%\n"
        "   900 тис · зазвичай 1,2 млн\n"
        "\n"
        "📤 Вигідно продати — дорожче, ніж зазвичай (1)\n"
        "🟡 Жвачка · Легендарный  +18%\n"
        "   47 млн · зазвичай 40 млн\n"
        "\n"
        f"Сигнал — відхилення від звичної ціни на {MARKET_SIGNAL_PCT:g}%+, "
        f"пари з {MARKET_MIN_LOTS}+ лотами. Ціни — медіана виставлених лотів.\n"
        "Усі сигнали, прайс і пошук артефакту — в боті 👇"
    )


def test_history_shorter_than_week_is_disclosed():
    history = days(2, **{"prism:2": (1_000, SOLID)})
    text = format_market_digest("EU", snap(**{"prism:2": (1_000, SOLID)}), history, DIGEST_NOW, DIGEST_CATALOG)
    assert f"середня за 2 дн. (історія збирається, повна — {MARKET_BASELINE_DAYS} дн.)" in text


def test_calm_market_message():
    history = days(7, **{"prism:2": (1_000, SOLID)})
    text = format_market_digest("EU", snap(**{"prism:2": (1_030, SOLID)}), history, DIGEST_NOW, DIGEST_CATALOG)
    assert "😴 Ринок спокійний" in text


def test_channel_shows_only_top_five_but_counts_all():
    prices_then = {f"a{i}:1": (1000, SOLID) for i in range(8)}
    prices_now = {f"a{i}:1": (500 - i, SOLID) for i in range(8)}
    catalog = tuple(Artifact(id=f"a{i}", name=f"Арт{i}", category="gravity") for i in range(8))
    text = format_market_digest("EU", snap(**prices_now), days(7, **prices_then), DIGEST_NOW, catalog)
    assert "(8)" in text
    assert text.count("· зазвичай") == 5  # рядки цін, без заголовка секції


def test_channel_post_fits_telegram_limit():
    prices_then = {f"a{i}:{q}": (1000, SOLID) for i in range(103) for q in range(6)}
    prices_now = {f"a{i}:{q}": (400 if i % 2 else 2000, SOLID) for i in range(103) for q in range(6)}
    catalog = tuple(Artifact(id=f"a{i}", name=f"Дуже довга назва артефакту {i}", category="gravity") for i in range(103))
    text = format_market_digest("EU", snap(**prices_now), days(7, **prices_then), DIGEST_NOW, catalog)
    assert telegram_len(text) <= 4096


def test_market_button_deep_link():
    assert market_button("SZ_EmissionsBot", "EU") == (
        "📊 Відкрити аналітику",
        "https://t.me/SZ_EmissionsBot?start=market_EU",
    )
    assert market_button(None, "EU") is None
