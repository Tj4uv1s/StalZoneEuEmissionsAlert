"""Топ артефактів, виставлених на аукціон після викиду.

На відміну від tracker.py, тут є мережа: ходимо по каталогу артефактів і
питаємо активні лоти кожного окремо. Загального фіда "всі лоти за період"
в API немає (сверено /reference — лоти тільки через /auction/{item}/lots),
тому доводиться перебирати відомі ID.

Беремо саме активні лоти, а не історію продажів: тільки в лотах є рідкість
(`additional.qlt`). Наслідок — лот, який встигли викупити до підрахунку, в
топ уже не потрапить: API його більше не показує.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

from app.artifacts import CATALOG, Artifact
from app.constants import (
    AUCTION_CONCURRENCY,
    AUCTION_MAX_PAGES,
    AUCTION_PAGE_SIZE,
    TOP_LOTS_COUNT,
)
from app.stalcraft import StalcraftClient
from app.tracker import parse_iso

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Listing:
    """Один виставлений на аукціон артефакт."""

    artifact: Artifact
    price: int
    quality: Optional[int]  # additional.qlt, 0..5; None, якщо API не віддав
    listed_at: str  # ISO-8601, коли лот виставили


def _price(lot: dict) -> int:
    """Ціна лота для рейтингу: викуп, а якщо його немає — поточна/стартова ставка."""
    return lot.get("buyoutPrice") or lot.get("currentPrice") or lot.get("startPrice") or 0


def _quality(lot: dict) -> Optional[int]:
    """Рідкість лота. None, якщо поля немає або воно не ціле число."""
    value = (lot.get("additional") or {}).get("qlt")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def _listings_between(
    api: StalcraftClient,
    region: str,
    artifact: Artifact,
    since: datetime,
    until: datetime,
    page_size: int,
    max_pages: int,
) -> list[Listing]:
    """Лоти одного артефакту, виставлені в проміжку [since, until].

    Лоти приходять найновіші першими, тож: свіжіші за `until` пропускаємо,
    а на першому старішому за `since` зупиняємось, не долистуючи решту.
    `max_pages` — запобіжник, щоб один надзвичайно популярний артефакт не
    з'їв увесь бюджет запитів прогону.
    """
    listings: list[Listing] = []
    offset = 0
    for _ in range(max_pages):
        data, _rate = await api.get_auction_lots(
            region, artifact.id, limit=page_size, offset=offset
        )
        lots = data.get("lots", [])
        if not lots:
            break

        reached_boundary = False
        for lot in lots:
            listed_at = parse_iso(lot.get("startTime"))
            if listed_at is None or listed_at < since:
                # Непридатний час — не знаємо, де ми відносно вікна; безпечніше зупинитись
                reached_boundary = True
                break
            if listed_at > until:
                continue
            listings.append(Listing(artifact, _price(lot), _quality(lot), lot["startTime"]))

        if reached_boundary or len(lots) < page_size:
            break
        offset += page_size

    return listings


async def top_listings_between(
    api: StalcraftClient,
    region: str,
    since: datetime,
    until: datetime,
    *,
    limit: int = TOP_LOTS_COUNT,
    catalog: tuple[Artifact, ...] = CATALOG,
    page_size: int = AUCTION_PAGE_SIZE,
    max_pages: int = AUCTION_MAX_PAGES,
    concurrency: int = AUCTION_CONCURRENCY,
) -> list[Listing]:
    """Найдорожчі артефакти, виставлені на аукціон у проміжку [since, until].

    Питає кожен артефакт із каталогу окремо, до `concurrency` одночасно.
    Один невдалий запит (мережа, 5xx після вичерпання ретраїв) не валить
    решту — артефакт пропускаємо.

    Args:
        api: Клієнт API
        region: Код регіону
        since: Початок вікна (кінець викиду)
        until: Кінець вікна
        limit: Скільки лотів повернути
        catalog: Каталог артефактів (параметр — для тестів)
        page_size: Розмір сторінки лотів за запит
        max_pages: Максимум сторінок на один артефакт
        concurrency: Скільки артефактів питати одночасно

    Returns:
        Лоти, відсортовані за спаданням ціни, не більше `limit` штук
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _safe(artifact: Artifact) -> list[Listing]:
        async with semaphore:
            try:
                return await _listings_between(
                    api, region, artifact, since, until, page_size, max_pages
                )
            except httpx.HTTPError:
                log.warning("%s: не вдалося отримати лоти аукціону", artifact.id, exc_info=True)
                return []

    # gather повертає результати в порядку каталогу — сортування нижче стабільне
    per_artifact = await asyncio.gather(*(_safe(artifact) for artifact in catalog))
    listings = [listing for group in per_artifact for listing in group]
    listings.sort(key=lambda listing: listing.price, reverse=True)
    return listings[:limit]
