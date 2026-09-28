"""Збір лотів аукціону по каталогу артефактів: топ після викиду й зріз ринку.

На відміну від tracker.py/market.py, тут є мережа: ходимо по каталогу
артефактів і питаємо активні лоти кожного окремо. Загального фіда "всі лоти"
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
from typing import Awaitable, Callable, Optional, TypeVar

import httpx

from app.artifacts import CATALOG, Artifact
from app.constants import (
    AUCTION_CONCURRENCY,
    AUCTION_MAX_PAGES,
    AUCTION_PAGE_SIZE,
    TOP_LOTS_COUNT,
)
from app.market import lot_quality
from app.stalcraft import StalcraftClient
from app.tracker import parse_iso

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class Listing:
    """Один виставлений на аукціон артефакт."""

    artifact: Artifact
    price: int
    quality: Optional[int]  # additional.qlt, 0..5; None, якщо API не віддав
    listed_at: str  # ISO-8601, коли лот виставили


@dataclass(frozen=True)
class MarketFetch:
    """Результат обходу всього ринку."""

    lots: dict[str, list[dict]]  # id артефакту -> активні лоти
    failed: int  # скільки артефактів не вдалося отримати
    total: int  # скільки артефактів питали


def _price(lot: dict) -> int:
    """Ціна лота для рейтингу: викуп, а якщо його немає — поточна/стартова ставка."""
    return lot.get("buyoutPrice") or lot.get("currentPrice") or lot.get("startPrice") or 0


async def _fetch_lots(
    api: StalcraftClient,
    region: str,
    artifact: Artifact,
    page_size: int,
    max_pages: int,
    stop_before: Optional[datetime] = None,
) -> list[dict]:
    """Активні лоти одного артефакту, найновіші першими.

    З `stop_before` зупиняємось на першому лоті, виставленому раніше (або з
    непридатним часом — тоді не знаємо, де ми відносно межі, і безпечніше
    зупинитись). Без нього — гортаємо всі сторінки. `max_pages` —
    запобіжник, щоб один надзвичайно популярний артефакт не з'їв увесь
    бюджет запитів прогону.
    """
    collected: list[dict] = []
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
            if stop_before is not None:
                listed_at = parse_iso(lot.get("startTime"))
                if listed_at is None or listed_at < stop_before:
                    reached_boundary = True
                    break
            collected.append(lot)

        if reached_boundary or len(lots) < page_size:
            break
        offset += page_size

    return collected


async def _per_artifact(
    catalog: tuple[Artifact, ...],
    concurrency: int,
    fetch: Callable[[Artifact], Awaitable[T]],
) -> list[Optional[T]]:
    """Виконати `fetch` для кожного артефакту, до `concurrency` одночасно.

    Невдалий запит (мережа, 5xx після вичерпання ретраїв) не валить решту —
    на місці такого артефакту буде None. Порядок результатів = порядок каталогу.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _safe(artifact: Artifact) -> Optional[T]:
        async with semaphore:
            try:
                return await fetch(artifact)
            except httpx.HTTPError:
                log.warning("%s: не вдалося отримати лоти аукціону", artifact.id, exc_info=True)
                return None

    return await asyncio.gather(*(_safe(artifact) for artifact in catalog))


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

    async def _window(artifact: Artifact) -> list[Listing]:
        lots = await _fetch_lots(api, region, artifact, page_size, max_pages, stop_before=since)
        return [
            Listing(artifact, _price(lot), lot_quality(lot), lot["startTime"])
            for lot in lots
            if parse_iso(lot["startTime"]) <= until  # свіжіші за вікно пропускаємо
        ]

    per_artifact = await _per_artifact(catalog, concurrency, _window)
    # порядок каталогу збережено — сортування нижче стабільне
    listings = [listing for group in per_artifact if group for listing in group]
    listings.sort(key=lambda listing: listing.price, reverse=True)
    return listings[:limit]


async def fetch_market(
    api: StalcraftClient,
    region: str,
    *,
    catalog: tuple[Artifact, ...] = CATALOG,
    page_size: int = AUCTION_PAGE_SIZE,
    max_pages: int = AUCTION_MAX_PAGES,
    concurrency: int = AUCTION_CONCURRENCY,
) -> MarketFetch:
    """Усі активні лоти по всьому каталогу — сирі дані для зрізу ринку.

    Кількість невдалих артефактів повертаємо окремо: якщо впала більшість,
    зріз вийде порожнім і "знищить" учорашні ціни — викликач має це бачити.

    Args:
        api: Клієнт API
        region: Код регіону
        catalog: Каталог артефактів (параметр — для тестів)
        page_size: Розмір сторінки лотів за запит
        max_pages: Максимум сторінок на один артефакт
        concurrency: Скільки артефактів питати одночасно

    Returns:
        Лоти по артефактах і статистика невдач
    """

    async def _all(artifact: Artifact) -> list[dict]:
        return await _fetch_lots(api, region, artifact, page_size, max_pages)

    per_artifact = await _per_artifact(catalog, concurrency, _all)
    lots = {
        artifact.id: result
        for artifact, result in zip(catalog, per_artifact)
        if result is not None
    }
    return MarketFetch(lots=lots, failed=len(catalog) - len(lots), total=len(catalog))
