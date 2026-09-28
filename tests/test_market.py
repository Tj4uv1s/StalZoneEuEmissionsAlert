"""Тести ринкової аналітики: зрізи, порівняння, розклад дайджесту."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.market import (
    Quote,
    add_to_history,
    baseline,
    build_snapshot,
    digest_due,
    lot_quality,
    market_history,
    price_key,
    quotes,
    signals,
    split_key,
    unit_price,
)

KYIV = ZoneInfo("Europe/Kyiv")


def lot(buyout, qlt=3, amount=1):
    return {"buyoutPrice": buyout, "amount": amount, "additional": {"qlt": qlt}}


# --- розбір лота ------------------------------------------------------------

@pytest.mark.parametrize(
    "additional,expected",
    [({"qlt": 4}, 4), ({"qlt": 0}, 0), ({}, None), (None, None), ({"qlt": "4"}, None), ({"qlt": True}, None)],
)
def test_lot_quality(additional, expected):
    assert lot_quality({"additional": additional}) == expected


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"buyoutPrice": 1000, "amount": 1}, 1000),
        ({"buyoutPrice": 1000, "amount": 4}, 250),  # стак — ціна за штуку
        ({"buyoutPrice": 1000}, 1000),  # amount відсутній — вважаємо 1
        ({"buyoutPrice": 0, "amount": 1}, None),  # лише ставка — не ціна
        ({"amount": 1}, None),
        ({"buyoutPrice": 1000, "amount": 0}, None),
        ({"buyoutPrice": "1000", "amount": 1}, None),
    ],
)
def test_unit_price(fields, expected):
    assert unit_price(fields) == expected


def test_key_roundtrip():
    assert split_key(price_key("zy7y", 5)) == ("zy7y", 5)


# --- зріз -------------------------------------------------------------------

def test_snapshot_median_and_count_per_quality():
    snapshot = build_snapshot(
        {
            "a1": [lot(100, 3), lot(300, 3), lot(200, 3), lot(9_000, 5)],
            "a2": [lot(50, 0)],
        }
    )
    assert snapshot == {
        "a1:3": {"median": 200, "count": 3},
        "a1:5": {"median": 9_000, "count": 1},
        "a2:0": {"median": 50, "count": 1},
    }


def test_median_resists_troll_prices():
    """Один лот «за 1» і один «за мільярд» не мають зсувати ціну."""
    snapshot = build_snapshot({"a1": [lot(1), lot(500), lot(510), lot(520), lot(1_000_000_000)]})
    assert snapshot["a1:3"]["median"] == 510


def test_even_count_median_is_rounded_int():
    snapshot = build_snapshot({"a1": [lot(100), lot(201)]})
    assert snapshot["a1:3"]["median"] == 150  # 150.5 -> 150 (банківське округлення)
    assert isinstance(snapshot["a1:3"]["median"], int)


def test_lots_without_quality_or_buyout_are_skipped():
    snapshot = build_snapshot(
        {"a1": [lot(0), {"buyoutPrice": 100, "amount": 1, "additional": {}}, lot(700)]}
    )
    assert snapshot == {"a1:3": {"median": 700, "count": 1}}


def test_empty_market_gives_empty_snapshot():
    assert build_snapshot({}) == {}
    assert build_snapshot({"a1": []}) == {}


# --- історія зрізів -----------------------------------------------------

def day(taken_at, **prices):
    """Денний зріз: day("2026-09-21T17:00:00Z", **{"a1:3": (100, 9)})."""
    return {
        "taken_at": taken_at,
        "prices": {key: {"median": m, "count": c} for key, (m, c) in prices.items()},
    }


def test_history_reads_new_format():
    history = [day("2026-09-21T17:00:00Z"), day("2026-09-22T17:00:00Z")]
    assert market_history({"market_history": history}) == history


def test_history_migrates_old_single_snapshot():
    """Оновлення бота не має обнуляти вже знятий зріз старого формату."""
    old = {"taken_at": "2026-09-28T09:00:00Z", "prices": {}}
    assert market_history({"market": old}) == [old]


@pytest.mark.parametrize(
    "state",
    [{}, {"market_history": "сміття"}, {"market_history": [None, 1, {"prices": {}}, {"taken_at": "не час", "prices": {}}]}],
)
def test_history_drops_broken_entries(state):
    assert market_history(state) == []


def test_add_to_history_keeps_last_n():
    history = [day(f"2026-09-2{i}T17:00:00Z") for i in range(1, 9)]
    new = add_to_history(history, {"a1:3": {"median": 1, "count": 9}}, "2026-09-29T17:00:00Z", keep=8)
    assert len(new) == 8
    assert new[0]["taken_at"] == "2026-09-22T17:00:00Z"
    assert new[-1]["taken_at"] == "2026-09-29T17:00:00Z"
    assert len(history) == 8  # вхідна історія не змінилась


# --- звична ціна --------------------------------------------------------

def test_baseline_is_mean_of_daily_medians():
    history = [
        day("2026-09-21T17:00:00Z", **{"a1:3": (100, 9)}),
        day("2026-09-22T17:00:00Z", **{"a1:3": (200, 9)}),
        day("2026-09-23T17:00:00Z", **{"a1:3": (300, 9)}),
    ]
    assert baseline(history, "a1:3", min_count=5) == (200, 3)


def test_baseline_skips_thin_days():
    history = [
        day("2026-09-21T17:00:00Z", **{"a1:3": (100, 9)}),
        day("2026-09-22T17:00:00Z", **{"a1:3": (99_999, 1)}),  # один лот — випадкова ціна
    ]
    assert baseline(history, "a1:3", min_count=5) == (100, 1)


def test_baseline_missing_pair_is_none():
    assert baseline([day("2026-09-21T17:00:00Z")], "a1:3", min_count=5) is None


# --- котирування й сигнали ------------------------------------------------

HISTORY = [
    day("2026-09-21T17:00:00Z", **{"cheap:2": (1000, 9), "dear:4": (1000, 9), "calm:1": (1000, 9), "thin:0": (1000, 9)}),
    day("2026-09-22T17:00:00Z", **{"cheap:2": (1000, 9), "dear:4": (1000, 9), "calm:1": (1000, 9), "thin:0": (1000, 9)}),
]
CURRENT = {
    "cheap:2": {"median": 750, "count": 9},   # −25% — купити
    "dear:4": {"median": 1250, "count": 9},   # +25% — продати
    "calm:1": {"median": 1050, "count": 9},   # +5% — нічого
    "thin:0": {"median": 100, "count": 2},    # −90%, але 2 лоти — не сигнал
    "new:5": {"median": 5000, "count": 9},    # немає бази — не сигнал
}


def test_quote_percent_against_baseline():
    by_id = {q.artifact_id: q for q in quotes(CURRENT, HISTORY, min_count=5)}
    assert by_id["cheap"].pct == -25.0
    assert by_id["cheap"].baseline == 1000
    assert by_id["cheap"].days == 2
    assert by_id["new"].pct is None
    assert by_id["new"].days == 0


def test_signals_buy_cheap_sell_dear_ignore_calm_thin_and_new():
    buys, sells = signals(quotes(CURRENT, HISTORY, min_count=5), threshold_pct=10, min_count=5)
    assert [q.artifact_id for q in buys] == ["cheap"]
    assert [q.artifact_id for q in sells] == ["dear"]


def test_signals_sorted_strongest_first():
    all_quotes = [
        Quote("a", 0, 90, 9, 100, 7),   # −10%
        Quote("b", 0, 50, 9, 100, 7),   # −50%
        Quote("c", 0, 130, 9, 100, 7),  # +30%
        Quote("d", 0, 200, 9, 100, 7),  # +100%
    ]
    buys, sells = signals(all_quotes, threshold_pct=10, min_count=5)
    assert [q.artifact_id for q in buys] == ["b", "a"]
    assert [q.artifact_id for q in sells] == ["d", "c"]


def test_signal_threshold_is_inclusive():
    buys, sells = signals([Quote("a", 0, 90, 9, 100, 7), Quote("b", 0, 91, 9, 100, 7)], 10, 5)
    assert [q.artifact_id for q in buys] == ["a"]


def test_quotes_skip_broken_current_entries():
    current = {"ok:1": {"median": 10, "count": 5}, "bad": {"median": 10, "count": 5}, "x:2": "сміття"}
    assert [q.artifact_id for q in quotes(current, [], min_count=5)] == ["ok"]


# --- розклад дайджесту -------------------------------------------------------

def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_first_digest_waits_for_the_hour():
    # 08:00 UTC = 11:00 Києва влітку — ще рано для 12:00
    assert digest_due(None, utc(2026, 9, 28, 8, 0), 12, KYIV) is False
    assert digest_due(None, utc(2026, 9, 28, 9, 0), 12, KYIV) is True


def test_not_twice_on_same_local_day():
    assert digest_due("2026-09-28T09:00:00Z", utc(2026, 9, 28, 20, 0), 12, KYIV) is False


def test_next_local_day_after_the_hour():
    assert digest_due("2026-09-28T09:00:00Z", utc(2026, 9, 29, 9, 0), 12, KYIV) is True
    assert digest_due("2026-09-28T09:00:00Z", utc(2026, 9, 29, 8, 59), 12, KYIV) is False


def test_local_day_boundary_not_utc():
    """Зріз о 23:30 Києва (20:30 UTC) і наступна доба Києва починається о 21:00 UTC."""
    last = "2026-09-28T20:30:00Z"  # 23:30 Києва, 28.09
    assert digest_due(last, utc(2026, 9, 28, 21, 30), 0, KYIV) is True  # 00:30 Києва, 29.09


def test_hour_is_local_across_dst():
    # Грудень: Київ UTC+2, тож 12:00 Києва = 10:00 UTC
    assert digest_due(None, utc(2026, 12, 28, 9, 59), 12, KYIV) is False
    assert digest_due(None, utc(2026, 12, 28, 10, 0), 12, KYIV) is True


def test_retry_at_postpones_attempt():
    now = utc(2026, 9, 28, 10, 0)
    assert digest_due(None, now, 12, KYIV, retry_at="2026-09-28T10:15:00Z") is False
    assert digest_due(None, now, 12, KYIV, retry_at="2026-09-28T09:45:00Z") is True


def test_broken_last_timestamp_is_treated_as_no_snapshot():
    assert digest_due("не час", utc(2026, 9, 28, 10, 0), 12, KYIV) is True

