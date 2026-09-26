"""Клієнт API гри. Тільки для читання даних про викиди (emission).

Авторизація — "Secret Based Authentication": заголовки Client-Id / Client-Secret
на кожен запит, без OAuth-обміну й рефреша. Цей режим розроблений для
застосунків, яким не потрібні дані конкретного гравця — нам підходить,
бо emission — це публічні дані.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx

from app.constants import DEFAULT_TIMEOUT, RETRY_ATTEMPTS, RETRY_BASE_DELAY
from app.http import default_ssl_context


@dataclass(frozen=True)
class RateLimit:
    """Розібрані заголовки X-RateLimit-* із відповіді API."""

    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset_at: Optional[float] = None  # unix-час (секунди), коли обнулиться окно

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> "RateLimit":
        """Побудувати RateLimit із заголовків відповіді."""

        def _int(name: str) -> Optional[int]:
            value = headers.get(name)
            return int(value) if value is not None else None

        reset_ms = headers.get("x-ratelimit-reset")
        return cls(
            limit=_int("x-ratelimit-limit"),
            remaining=_int("x-ratelimit-remaining"),
            reset_at=int(reset_ms) / 1000 if reset_ms is not None else None,
        )


class StalcraftClient:
    """HTTP-клієнт для гри STALCRAFT API."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Ініціалізація клієнта.

        Args:
            base_url: Базова URL API (https://eapi.stalcraft.net або https://dapi.stalcraft.net)
            client_id: Ідентифікатор застосунку
            client_secret: Секрет застосунку
            timeout: Таймаут запитів у секундах
        """
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Client-Id": client_id, "Client-Secret": client_secret},
            timeout=timeout,
            verify=default_ssl_context(),
        )

    async def _get(
        self, path: str, params: dict | None, retries: int
    ) -> tuple[dict, RateLimit]:
        """Спільна логіка запиту й повтору для всіх ендпоінтів клієнта.

        Повторюємо тільки те, що має сенс повторювати: мережеві обриви, 5xx і 429
        (з уважністю до Retry-After). На 4xx (неправильний токен, помилка в регіоні
        тощо) падаємо одразу — повтор не допоможе.

        Args:
            path: Шлях запиту (з провідним /)
            params: Query-параметри або None
            retries: Кількість спроб повтору

        Returns:
            (response_dict, RateLimit): Дані від API та інформація про rate limit
        """
        for attempt in range(retries + 1):
            try:
                resp = await self._client.get(path, params=params)
            except httpx.HTTPError:
                if attempt == retries:
                    raise
                await asyncio.sleep(RETRY_BASE_DELAY**attempt)
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "3"))
                await asyncio.sleep(retry_after)
                continue

            if resp.status_code >= 500:
                if attempt == retries:
                    resp.raise_for_status()
                await asyncio.sleep(RETRY_BASE_DELAY**attempt)
                continue

            resp.raise_for_status()  # 4xx, крім 429 — падаємо одразу, без повтору
            return resp.json(), RateLimit.from_headers(resp.headers)

        raise RuntimeError("unreachable")

    async def get_emission(
        self, region: str, retries: int = RETRY_ATTEMPTS
    ) -> tuple[dict, RateLimit]:
        """Отримати дані про викид у регіоні.

        GET /{region}/emission -> ({currentStart?, previousStart?, previousEnd?}, RateLimit)

        Args:
            region: Код регіону (EU, RU, NA, SEA, NEA)
            retries: Кількість спроб повтору

        Returns:
            (response_dict, RateLimit): Дані від API та інформація про rate limit
        """
        return await self._get(f"/{region}/emission", None, retries)

    async def get_auction_lots(
        self,
        region: str,
        item_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        retries: int = RETRY_ATTEMPTS,
    ) -> tuple[dict, RateLimit]:
        """Отримати активні лоти предмета, найновіші перші.

        GET /{region}/auction/{item}/lots -> ({total, lots: [...]}, RateLimit)

        Завжди з additional=true: тільки там є `qlt` (рідкість) — в історії
        продажів її немає зовсім.

        Args:
            region: Код регіону
            item_id: Ідентифікатор предмета (з каталогу артефактів)
            limit: Скільки лотів повернути, максимум 200
            offset: Скільки лотів пропустити з початку
            retries: Кількість спроб повтору

        Returns:
            (response_dict, RateLimit): Дані від API та інформація про rate limit
        """
        params = {
            "limit": limit,
            "offset": offset,
            "sort": "time_created",
            "order": "desc",
            "additional": "true",
        }
        return await self._get(f"/{region}/auction/{item_id}/lots", params, retries)

    async def aclose(self) -> None:
        """Закрити HTTP-клієнт."""
        await self._client.aclose()

    async def __aenter__(self) -> "StalcraftClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()
