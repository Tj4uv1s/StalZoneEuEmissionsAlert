"""Відправлення повідомлень у Telegram прямо через Bot API (без фреймворку).

Фреймворк (aiogram) додамо пізніше, коли з'являться команди від користувачів.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from datetime import timedelta, timezone

from app.constants import (
    MSK_LABEL,
    MSK_OFFSET_HOURS,
    TELEGRAM_API_BASE,
    TELEGRAM_RETRY_AFTER_DEFAULT,
    TELEGRAM_SEND_METHOD,
)
from app.http import default_ssl_context
from app.tracker import ENDED, WARNING, Event, parse_iso

log = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=MSK_OFFSET_HOURS))

_TEXT = {
    WARNING: "⚠️ Викид у регіоні {region}!",
    ENDED: "✅ Викид у регіоні {region} завершився.",
}


def _format_time(iso_time: str | None) -> str | None:
    """Відформатувати ISO-8601 час як `HH:MM МСК`. None, якщо час непридатний."""
    moment = parse_iso(iso_time)
    if moment is None:
        return None
    return f"{moment.astimezone(_MSK).strftime('%H:%M')} {MSK_LABEL}"


def _format_duration(seconds: float) -> str:
    """Відформатувати тривалість у хвилини/години."""
    total_minutes = int(seconds) // 60
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours} г {minutes} хв" if hours else f"{minutes} хв"


def format_event(event: Event) -> str:
    """Відформатувати подію в текст для Telegram.

    Рядки з непридатним часом просто пропускаються — краще коротше
    повідомлення, ніж падіння на форматуванні.

    Args:
        event: Подія трекера

    Returns:
        Відформатований текст повідомлення
    """
    if event.kind == WARNING:
        at_label, predicted_label = "⏰ Почало", "📍 Орієнтовно закінчиться"
    elif event.kind == ENDED:
        at_label, predicted_label = "⏰ Закінчено", "📍 Наступний орієнтовно"
    else:
        return f"{event.region}: {event.kind} {event.at}"

    lines = [_TEXT[event.kind].format(region=event.region)]

    at_time = _format_time(event.at)
    if at_time:
        lines.append(f"{at_label}: {at_time}")

    if event.interval is not None:
        duration = _format_duration(event.interval)
        if event.kind == WARNING:
            lines.append(f"⏸️ З попереднього викиду: {duration}")
        else:
            lines.append(f"⌛ Тривав: {duration}")

    predicted_time = _format_time(event.predicted_next)
    if predicted_time:
        lines.append(f"{predicted_label}: {predicted_time}")

    return "\n".join(lines)


class Notifier:
    """Відправник повідомлень у Telegram.

    Якщо token/chat_ids не задані — працює в режимі "тільки консоль".
    Це навмисне: дає обкатати зв'язку API+tracker до того, як заведений
    Telegram-бот і відомі id чатів.
    """

    def __init__(self, token: str, chat_ids: list[str], timeout: float = 15.0) -> None:
        """Ініціалізація відправника.

        Args:
            token: Telegram Bot API токен
            chat_ids: Список ID чатів/каналів для отримання повідомлень
            timeout: Таймаут запитів у секундах
        """
        self._chat_ids = chat_ids
        self._client: httpx.AsyncClient | None = None
        if token and chat_ids:
            self._client = httpx.AsyncClient(
                base_url=f"{TELEGRAM_API_BASE}/bot{token}",
                timeout=timeout,
                verify=default_ssl_context(),
            )
        else:
            log.warning(
                "TG_TOKEN/TG_CHAT_IDS не задані — подій тільки логуються, у Telegram не йдуть"
            )

    async def _send(self, chat_id: str, text: str) -> None:
        """Відправити повідомлення в один чат.

        Args:
            chat_id: ID чату або юзернейм каналу
            text: Текст повідомлення
        """
        assert self._client is not None
        resp = await self._client.post(
            f"/{TELEGRAM_SEND_METHOD}",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", TELEGRAM_RETRY_AFTER_DEFAULT)
            await asyncio.sleep(retry_after)
            await self._send(chat_id, text)
            return
        resp.raise_for_status()

    async def broadcast(self, text: str) -> None:
        """Розіслати повідомлення у всі чати.

        Args:
            text: Текст повідомлення
        """
        if self._client is None:
            return
        for chat_id in self._chat_ids:
            try:
                await self._send(chat_id, text)
            except httpx.HTTPStatusError as exc:
                log.error("не змогла відправити в %s: %s", chat_id, exc)

    async def aclose(self) -> None:
        """Закрити HTTP-клієнт."""
        if self._client is not None:
            await self._client.aclose()
