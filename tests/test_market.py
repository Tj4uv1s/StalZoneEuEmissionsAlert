"""Тести ринкової аналітики: зрізи, порівняння, розклад дайджесту."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.market import (
    PriceChange,
    build_snapshot,
    compare,
    digest_due,
    lot_quality,
    price_key,
    split_key,
    top_movers,
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


# --- порівняння -------------------------------------------------------------

def entry(median, count=5):
    return {"median": median, "count": count}


def test_compare_percent_change():
    changes = compare({"a1:3": entry(100)}, {"a1:3": entry(125)}, min_count=3)
    assert changes == [PriceChange("a1", 3, 100, 125, 25.0)]


def test_compare_skips_thin_markets_on_either_day():
    previous = {"a1:3": entry(100, count=2), "a2:3": entry(100, count=5)}
    current = {"a1:3": entry(900, count=5), "a2:3": entry(900, count=2)}
    assert compare(previous, current, min_count=3) == []


def test_compare_skips_pairs_missing_on_one_side():
    changes = compare({"old:1": entry(100)}, {"new:1": entry(100)}, min_count=3)
    assert changes == []


@pytest.mark.parametrize(
    "bad",
    [entry(0), {"median": "100", "count": 5}, {"count": 5}, "сміття", None, {"median": 100}],
)
def test_compare_ignores_broken_previous_entries(bad):
    assert compare({"a1:3": bad}, {"a1:3": entry(100)}, min_count=3) == []


def test_compare_ignores_broken_keys_in_old_state():
    assert compare({"без-двокрапки": entry(100)}, {"без-двокрапки": entry(200)}, 3) == []


def test_top_movers_split_and_order():
    changes = [
        PriceChange("a", 0, 100, 150, 50.0),
        PriceChange("b", 0, 100, 110, 10.0),
        PriceChange("c", 0, 100, 60, -40.0),
        PriceChange("d", 0, 100, 95, -5.0),
        PriceChange("e", 0, 100, 100, 0.0),  # без змін — ні туди, ні туди
    ]
    gainers, losers = top_movers(changes, limit=10)
    assert [c.artifact_id for c in gainers] == ["a", "b"]
    assert [c.artifact_id for c in losers] == ["c", "d"]


def test_top_movers_respects_limit():
    changes = [PriceChange(str(i), 0, 100, 100 + i, float(i)) for i in range(1, 20)]
    gainers, losers = top_movers(changes, limit=3)
    assert [c.pct for c in gainers] == [19.0, 18.0, 17.0]
    assert losers == []


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


def test_top_movers_drops_noise_below_min_pct():
    changes = [
        PriceChange("big-up", 0, 100, 120, 20.0),
        PriceChange("tiny-up", 0, 1000, 1004, 0.4),
        PriceChange("tiny-down", 0, 1000, 997, -0.3),
        PriceChange("big-down", 0, 100, 80, -20.0),
        PriceChange("edge", 0, 100, 101, 1.0),  # рівно на межі — лишаємо
    ]
    gainers, losers = top_movers(changes, limit=10, min_pct=1.0)
    assert [c.artifact_id for c in gainers] == ["big-up", "edge"]
    assert [c.artifact_id for c in losers] == ["big-down"]
