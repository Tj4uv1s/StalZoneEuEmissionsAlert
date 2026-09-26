"""Збір статистики продажів артефактів з аукціону.

На відміну від tracker.py, тут є мережа: ходимо по каталогу артефактів і
питаємо історію продажів кожного окремо. Загального фіда "всі продажі за
період" в API немає (сверено /reference — тільки /auction/{item}/history),
тому доводиться перебирати відомі ID.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.artifacts import CATALOG, Artifact
from app.constants import AUCTION_MAX_PAGES, AUCTION_PAGE_SIZE, TOP_SALES_COUNT
from app.stalcraft import StalcraftClient
from app.tracker import parse_iso

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sale:
    """Один продаж артефакту на аукціоні."""

    artifact: Artifact
    price: int
    amount: int
    at: str  # ISO-8601, час покупки


async def _sales_since(
    api: StalcraftClient,
    region: str,
    artifact: Artifact,
    since: datetime,
    page_size: int,
    max_pages: int,
) -> list[Sale]:
    """Продажі одного артефакту не раніше `since`.

    Історія відсортована за спаданням часу, тож зупиняємось, щойно впираємось
    у запис старіший за `since`, не долистуючи решту. `max_pages` — запобіжник:
    без нього надзвичайно ліквідний артефакт (тисячі продажів) міг би з'їсти
    весь бюджет запитів цього прогону.
    """
    sales: list[Sale] = []
    offset = 0
    for _ in range(max_pages):
        data, _rate = await api.get_auction_history(
            region, artifact.id, limit=page_size, offset=offset
        )
        prices = data.get("prices", [])
        if not prices:
            break

        reached_boundary = False
        for entry in prices:
            sold_at = parse_iso(entry.get("time"))
            if sold_at is None or sold_at < since:
                reached_boundary = True
                break
            sales.append(
                Sale(artifact, entry.get("price", 0), entry.get("amount", 1), entry["time"])
            )

        if reached_boundary or len(prices) < page_size:
            break
        offset += page_size

    return sales


async def top_sales_since(
    api: StalcraftClient,
    region: str,
    since: datetime,
    *,
    limit: int = TOP_SALES_COUNT,
    catalog: tuple[Artifact, ...] = CATALOG,
    page_size: int = AUCTION_PAGE_SIZE,
    max_pages: int = AUCTION_MAX_PAGES,
) -> list[Sale]:
    """Топ продажів артефактів у регіоні, починаючи з `since`.

    Питає кожен артефакт із каталогу окремо. Один невдалий запит (мережа,
    5xx після вичерпання ретраїв) не валить решту — просто пропускаємо
    артефакт і йдемо далі.

    Args:
        api: Клієнт API
        region: Код регіону
        since: Не враховувати продажі раніше цього моменту
        limit: Скільки найдорожчих продажів повернути
        catalog: Каталог артефактів (параметр — для тестів)
        page_size: Розмір сторінки історії за запит
        max_pages: Максимум сторінок на один артефакт

    Returns:
        Продажі, відсортовані за спаданням ціни, не більше `limit` штук
    """
    all_sales: list[Sale] = []
    for artifact in catalog:
        try:
            all_sales.extend(
                await _sales_since(api, region, artifact, since, page_size, max_pages)
            )
        except httpx.HTTPError:
            log.warning("%s: не вдалося отримати історію аукціону", artifact.id, exc_info=True)

    all_sales.sort(key=lambda sale: sale.price, reverse=True)
    return all_sales[:limit]
