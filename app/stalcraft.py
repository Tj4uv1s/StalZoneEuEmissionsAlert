"""Клиент API игры. Пока используем только эндпоинт emission."""
from __future__ import annotations

import asyncio

import httpx


class StalcraftClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    async def get_emission(self, region: str, retries: int = 2) -> dict:
        """GET /{region}/emission -> {currentStart, previousStart, previousEnd}."""
        for attempt in range(retries + 1):
            try:
                resp = await self._client.get(f"/{region}/emission")
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError,) as exc:
                if attempt == retries:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "StalcraftClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()
