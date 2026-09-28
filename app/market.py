"""Ринкова аналітика: зрізи цін артефактів і порівняння між днями.

Як і tracker.py — чиста логіка: ні мережі, ні файлів, ні читання годинника.
Лоти й поточний час приходять аргументами, тому все покривається тестами.

"Ціна" пари (артефакт, рідкість) — медіана ціни викупу серед її активних
лотів. Не мінімум: один лот "за 1" чи троль "за 999 млн" ламали б аналітику.
Не історія продажів: у ній немає рідкості. Тож це медіана ЗАПИТУВАНИХ цін,
а не цін реальних угод — ринок пропозиції, а не угод.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from statistics import median
from typing import Optional

from app.tracker import parse_iso


@dataclass(frozen=True)
class PriceChange:
    """Зміна медіанної ціни пари (артефакт, рідкість) між двома зрізами."""

    artifact_id: str
    quality: int
    old: int
    new: int
    pct: float  # (new - old) / old * 100


def lot_quality(lot: dict) -> Optional[int]:
    """Рідкість лота (additional.qlt). None, якщо поля немає або воно не ціле число."""
    value = (lot.get("additional") or {}).get("qlt")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def unit_price(lot: dict) -> Optional[int]:
    """Ціна викупу за одну штуку. None для лотів без викупу (тільки ставки).

    Ставка — не ціна: поки аукціон не закінчився, вона може бути будь-якою
    малою, і медіана по ставках тягнула б ціну вниз.
    """
    buyout = lot.get("buyoutPrice")
    amount = lot.get("amount", 1)  # не `or 1`: amount=0 — криві дані, а не одна штука
    if not isinstance(buyout, int) or buyout <= 0 or not isinstance(amount, int) or amount < 1:
        return None
    return buyout // amount


def price_key(artifact_id: str, quality: int) -> str:
    """Ключ пари в зрізі. Рядок, бо зріз лежить у JSON-стані."""
    return f"{artifact_id}:{quality}"


def split_key(key: str) -> tuple[str, int]:
    """Зворотне до price_key."""
    artifact_id, quality = key.rsplit(":", 1)
    return artifact_id, int(quality)


def build_snapshot(lots_by_artifact: dict[str, list[dict]]) -> dict[str, dict]:
    """Зріз цін: для кожної пари (артефакт, рідкість) — медіана й кількість лотів.

    Лоти без рідкості чи без викупу пропускаються.

    Args:
        lots_by_artifact: id артефакту -> його активні лоти, як їх віддає API

    Returns:
        {"<id>:<qlt>": {"median": int, "count": int}}
    """
    grouped: dict[str, list[int]] = {}
    for artifact_id, lots in lots_by_artifact.items():
        for lot in lots:
            quality, price = lot_quality(lot), unit_price(lot)
            if quality is None or price is None:
                continue
            grouped.setdefault(price_key(artifact_id, quality), []).append(price)
    return {
        key: {"median": round(median(prices)), "count": len(prices)}
        for key, prices in grouped.items()
    }


def _usable(entry: object, min_count: int) -> bool:
    """Чи достатньо даних у записі зрізу, щоб на нього спиратись."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("median"), int)
        and entry["median"] > 0
        and isinstance(entry.get("count"), int)
        and entry["count"] >= min_count
    )


def compare(previous: dict, current: dict, min_count: int) -> list[PriceChange]:
    """Зміни цін між двома зрізами.

    Порівнюємо лише пари, де лотів було не менше `min_count` в ОБИДВА дні:
    на тонкому ринку один лот дає стрибки на сотні відсотків, і такий шум
    забив би весь топ змін. Пари, яких немає в одному зі зрізів, пропускаються.

    Args:
        previous: Попередній зріз (build_snapshot)
        current: Поточний зріз
        min_count: Мінімум лотів у парі в кожному зрізі

    Returns:
        Зміни без певного порядку
    """
    changes = []
    for key, now_entry in current.items():
        before = previous.get(key)
        if not (_usable(before, min_count) and _usable(now_entry, min_count)):
            continue
        try:
            artifact_id, quality = split_key(key)
        except ValueError:
            continue  # кривий ключ у старому стані
        old, new = before["median"], now_entry["median"]
        changes.append(PriceChange(artifact_id, quality, old, new, (new - old) / old * 100))
    return changes


def top_movers(
    changes: list[PriceChange], limit: int, min_pct: float = 0.0
) -> tuple[list[PriceChange], list[PriceChange]]:
    """Найбільше подорожчали й найбільше подешевшали.

    Args:
        changes: Зміни (compare)
        limit: Скільки показати в кожному напрямку
        min_pct: Зміни, менші за модулем, вважаємо шумом і відкидаємо

    Returns:
        (зростання за спаданням %, падіння від найглибшого), не більше `limit` кожне
    """
    threshold = max(min_pct, 0.0)
    gainers = sorted(
        (c for c in changes if c.pct > 0 and c.pct >= threshold),
        key=lambda c: c.pct,
        reverse=True,
    )
    losers = sorted((c for c in changes if c.pct < 0 and -c.pct >= threshold), key=lambda c: c.pct)
    return gainers[:limit], losers[:limit]


def digest_due(
    last_taken_at: Optional[str],
    now: datetime,
    hour: int,
    tz: tzinfo,
    retry_at: Optional[str] = None,
) -> bool:
    """Чи час робити дайджест: раз на місцеву добу, не раніше години `hour`.

    Година — місцева (київська), тож дайджест виходить в одну й ту саму
    годину для читачів незалежно від переходу на літній/зимовий час.

    Args:
        last_taken_at: Коли знімали попередній зріз (ISO) або None
        now: Поточний час
        hour: Місцева година, з якої дайджест можна робити
        tz: Місцева часова зона
        retry_at: Не раніше цього моменту (ISO) — після невдалої спроби

    Returns:
        True, якщо дайджест пора робити
    """
    local_now = now.astimezone(tz)
    if local_now.hour < hour:
        return False
    retry = parse_iso(retry_at)
    if retry is not None and now < retry:
        return False
    last = parse_iso(last_taken_at)
    return last is None or last.astimezone(tz).date() < local_now.date()
