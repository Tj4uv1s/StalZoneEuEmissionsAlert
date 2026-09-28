"""Звіти, спільні для планувальника (main.py) і ручного запуску (cli.py).

Тут тільки "зібрати й відформатувати": ні стану, ні розкладу, ні відправки.
Коли звіт виходить і що з ним робити далі — вирішує той, хто викликає.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.auction import fetch_market, top_listings_between
from app.market import build_snapshot
from app.notify import format_market_digest, format_top_listings
from app.stalcraft import StalcraftClient
from app.tracker import parse_iso


class MarketUnavailable(RuntimeError):
    """Обхід ринку здебільшого впав — зріз був би майже порожнім і оманливим."""


@dataclass(frozen=True)
class Digest:
    """Готовий дайджест: повідомлення для відправки й зріз, з якого його зібрано."""

    messages: list[str]
    snapshot: dict


async def build_top_report(
    api: StalcraftClient, region: str, since: datetime, until: datetime
) -> Optional[str]:
    """Топ виставлених на аукціон артефактів за вікно. None, якщо нічого не виставляли."""
    listings = await top_listings_between(api, region, since, until)
    return format_top_listings(region, listings, since, until)


async def build_digest(
    api: StalcraftClient, region: str, market: dict, now: datetime
) -> Digest:
    """Зріз ринку зараз і дайджест проти збереженого зрізу `market`.

    Args:
        api: Клієнт API
        region: Код регіону
        market: Збережений зріз {"taken_at", "prices"} або {} — тоді прайс
        now: Поточний час

    Raises:
        MarketUnavailable: якщо не вдалося отримати більшість артефактів
    """
    fetched = await fetch_market(api, region)
    if fetched.failed * 2 > fetched.total:
        raise MarketUnavailable(
            f"{region}: не вдалося отримати лоти {fetched.failed} з {fetched.total} артефактів"
        )
    snapshot = build_snapshot(fetched.lots)
    messages = format_market_digest(
        region, snapshot, market.get("prices"), market.get("taken_at"), now
    )
    return Digest(messages, snapshot)


async def last_emission_window(
    api: StalcraftClient, region: str, now: datetime, delay: float
) -> Optional[tuple[datetime, datetime]]:
    """Вікно топу лотів для ОСТАННЬОГО завершеного викиду — для ручного запуску.

    Те саме вікно, що й у планувальника: [кінець викиду, кінець + delay]. Якщо
    з кінця викиду ще не минуло `delay`, вікно обрізається до `now`.

    Returns:
        (since, until) або None, якщо API не віддав час кінця викиду
    """
    response, _rate = await api.get_emission(region)
    end = parse_iso(response.get("previousEnd"))
    if end is None:
        return None
    return end, min(end + timedelta(seconds=delay), now)
