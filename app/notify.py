"""Отправка сообщений в Telegram напрямую через Bot API (без фреймворка).

Фреймворк (aiogram) добавим позже, когда появятся команды от пользователей.
"""
from __future__ import annotations

import asyncio

import httpx

from app.tracker import ENDED, WARNING, Event

_TEXT = {
    WARNING: "⚠️ Выброс в регионе {region}!\nНачало: {at}",
    ENDED: "✅ Выброс в регионе {region} завершился.\nОкончание: {at}",
}


def format_event(event: Event) -> str:
    template = _TEXT.get(event.kind, "{region}: {kind} {at}")
    return template.format(region=event.region, at=event.at, kind=event.kind)


class Notifier:
    def __init__(self, token: str, chat_ids: list[str], timeout: float = 15.0) -> None:
        self._chat_ids = chat_ids
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=timeout,
        )

    async def _send(self, chat_id: str, text: str) -> None:
        resp = await self._client.post(
            "/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 3)
            await asyncio.sleep(retry_after)
            await self._send(chat_id, text)
            return
        resp.raise_for_status()

    async def broadcast(self, text: str) -> None:
        for chat_id in self._chat_ids:
            try:
                await self._send(chat_id, text)
            except httpx.HTTPStatusError as exc:
                print(f"[notify] не смог отправить в {chat_id}: {exc}")

    async def aclose(self) -> None:
        await self._client.aclose()
