"""Ринкова аналітика: зрізи цін, "звична" ціна за тиждень, сигнали купити/продати.

Як і tracker.py — чиста логіка: ні мережі, ні файлів, ні читання годинника.
Лоти, історія й поточний час приходять аргументами, тому все покривається тестами.

"Ціна" пари (артефакт, рідкість) — медіана ціни викупу серед її активних
лотів. Не мінімум: один лот "за 1" чи троль "за 999 млн" ламали б аналітику.
Не історія продажів: у ній немає рідкості. Тож це медіана ЗАПИТУВАНИХ цін,
а не цін реальних угод — ринок пропозиції, а не угод.

"Звична" ціна (база) — середнє денних медіан за попередні дні (до тижня).
Сигнал — відхилення поточної ціни від бази: помітно дешевше за звичне —
вигідно купити, помітно дорожче — вигідно продати. База за тиждень, а не
вчорашній день: одна випадкова пачка лотів не дає хибного "купуй".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from statistics import mean, median
from typing import Optional

from app.tracker import parse_iso


@dataclass(frozen=True)
class Quote:
    """Поточна ціна пари (артефакт, рідкість) проти її звичної ціни."""

    artifact_id: str
    quality: int
    price: int  # медіана зараз
    count: int  # скільки лотів зараз
    baseline: Optional[int]  # звична ціна (середнє денних медіан) або None
    days: int  # за скільки днів порахована база

    @property
    def pct(self) -> Optional[float]:
        """Відхилення від звичної ціни, %. None, якщо бази немає."""
        if not self.baseline:
            return None
        return (self.price - self.baseline) / self.baseline * 100


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


def market_history(state: dict) -> list[dict]:
    """Денні зрізи регіону зі стану, найстаріший першим: [{"taken_at", "prices"}].

    Читає й старий формат (єдиний зріз у "market"), щоб оновлення бота не
    обнуляло накопичену історію. Криві записи відкидаються.
    """
    raw = state.get("market_history")
    if raw is None and isinstance(state.get("market"), dict):
        raw = [state["market"]]
    return [
        entry
        for entry in (raw if isinstance(raw, list) else [])
        if isinstance(entry, dict)
        and isinstance(entry.get("prices"), dict)
        and parse_iso(entry.get("taken_at")) is not None
    ]


def add_to_history(
    history: list[dict], snapshot: dict, taken_at: str, keep: int
) -> list[dict]:
    """Нова історія: старі зрізи + сьогоднішній, не більше `keep` останніх."""
    return [*history, {"taken_at": taken_at, "prices": snapshot}][-keep:]


def baseline(history: list[dict], key: str, min_count: int) -> Optional[tuple[int, int]]:
    """Звична ціна пари: середнє її денних медіан у `history`.

    Дні, де пара була на тонкому ринку (менше `min_count` лотів), не
    враховуються — там медіана випадкова.

    Returns:
        (звична ціна, скільки днів врахували) або None, якщо жодного дня немає
    """
    medians = [
        day["prices"][key]["median"]
        for day in history
        if _usable(day["prices"].get(key), min_count)
    ]
    return (round(mean(medians)), len(medians)) if medians else None


def quotes(current: dict, history: list[dict], min_count: int) -> list[Quote]:
    """Поточні ціни всіх пар зі зрізу разом із їхніми звичними цінами.

    Args:
        current: Сьогоднішній зріз (build_snapshot)
        history: Попередні денні зрізи (без сьогоднішнього)
        min_count: Мінімум лотів, щоб день ішов у базу

    Returns:
        Котирування без певного порядку (криві ключі зрізу пропускаються)
    """
    result = []
    for key, entry in current.items():
        if not _usable(entry, 1):
            continue
        try:
            artifact_id, quality = split_key(key)
        except ValueError:
            continue
        base = baseline(history, key, min_count)
        result.append(
            Quote(
                artifact_id,
                quality,
                entry["median"],
                entry["count"],
                base[0] if base else None,
                base[1] if base else 0,
            )
        )
    return result


def is_solid(quote: Quote, min_count: int) -> bool:
    """Чи досить лотів ЗАРАЗ, щоб поточна ціна щось означала."""
    return quote.count >= min_count


def signals(
    all_quotes: list[Quote], threshold_pct: float, min_count: int
) -> tuple[list[Quote], list[Quote]]:
    """Сигнали: що зараз помітно дешевше і що помітно дорожче за звичне.

    Беруться тільки пари з базою й досить товстим ринком зараз: на тонкому
    ринку один лот дає "−60%", і гравці купували б за хибним сигналом.

    Returns:
        (купити — від найбільшої знижки, продати — від найбільшого зростання)
    """
    tradable = [
        q for q in all_quotes if q.pct is not None and is_solid(q, min_count)
    ]
    buys = sorted((q for q in tradable if q.pct <= -threshold_pct), key=lambda q: q.pct)
    sells = sorted(
        (q for q in tradable if q.pct >= threshold_pct), key=lambda q: q.pct, reverse=True
    )
    return buys, sells


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
