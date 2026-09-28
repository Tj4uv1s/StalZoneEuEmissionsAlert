"""Повідомлення в Telegram: форматування постів каналу й відправлення через Bot API.

Відправлення в канал — прямо через Bot API (httpx). Інтерактивне меню в
особистих повідомленнях живе окремо, в app/telegram_bot.py (aiogram).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from app.artifacts import CATALOG, Artifact
from app.auction import Listing
from app.constants import (
    CHANNEL_SIGNALS_COUNT,
    DEEP_LINK_PREFIX,
    LOCAL_TZ_LABEL,
    MARKET_BASELINE_DAYS,
    MARKET_MIN_LOTS,
    MARKET_SIGNAL_PCT,
    TELEGRAM_API_BASE,
    TELEGRAM_RETRY_AFTER_DEFAULT,
    TELEGRAM_SEND_METHOD,
)
from app.formatting import (
    LOCAL_TZ,
    clock,
    format_compact,
    format_pct,
    format_price,
    quality_title,
)
from app.http import default_ssl_context
from app.market import Quote, quotes, signals
from app.tracker import ENDED, WARNING, Event, parse_iso

log = logging.getLogger(__name__)

_TEXT = {
    WARNING: "⚠️ Викид у регіоні {region}!",
    ENDED: "✅ Викид у регіоні {region} завершився.",
}

_MEDALS = ("🥇", "🥈", "🥉")

Button = tuple[str, str]  # (текст, url)


def _format_time(iso_time: str | None) -> str | None:
    """Відформатувати ISO-8601 час як `HH:MM за Києвом`. None, якщо непридатний."""
    moment = parse_iso(iso_time)
    return f"{clock(moment)} {LOCAL_TZ_LABEL}" if moment else None


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


def format_top_listings(
    region: str, listings: list[Listing], since: datetime, until: datetime
) -> str | None:
    """Відформатувати топ виставлених на аукціон артефактів у текст для Telegram.

    Колір рядка — рідкість лота (qlt): Telegram не вміє фарбувати текст,
    тому колір передаємо кружечком-емодзі.

    Args:
        region: Код регіону
        listings: Лоти, відсортовані за спаданням ціни (top_listings_between)
        since: Початок вікна (кінець викиду)
        until: Кінець вікна

    Returns:
        Текст повідомлення, або None, якщо нічого не виставили (тоді нема про що писати)
    """
    if not listings:
        return None

    lines = [
        f"📦 Топ-{len(listings)} артефактів, виставлених на аукціон у регіоні {region}",
        f"🕒 {clock(since)}–{clock(until)} {LOCAL_TZ_LABEL}",
        "",
    ]
    for index, listing in enumerate(listings):
        rank = _MEDALS[index] if index < len(_MEDALS) else f"{index + 1}."
        title = quality_title(listing.artifact.name, listing.quality)
        lines.append(f"{rank} {title} — {format_price(listing.price)}")

    return "\n".join(lines)


def signal_lines(quote: Quote, names: dict[str, str]) -> list[str]:
    """Два рядки сигналу: що це і наскільки ціна відійшла від звичної.

    🔵 Призма · Особый  −25%
       900 тис · зазвичай 1,2 млн
    """
    title = quality_title(names.get(quote.artifact_id, quote.artifact_id), quote.quality)
    return [
        f"{title}  {format_pct(quote.pct)}",
        f"   {format_compact(quote.price)} · зазвичай {format_compact(quote.baseline)}",
    ]


def baseline_note(history: list[dict]) -> str:
    """Пояснення, з чим порівнюємо: скільки днів уже в базі."""
    days = len(history)
    if days >= MARKET_BASELINE_DAYS:
        return f"Звична ціна — середня за {days} дн."
    return (
        f"Звична ціна — середня за {days} дн. "
        f"(історія збирається, повна — {MARKET_BASELINE_DAYS} дн.)"
    )


def format_market_digest(
    region: str,
    current: dict,
    history: list[dict],
    now: datetime,
    catalog: tuple[Artifact, ...] = CATALOG,
) -> str:
    """Пост каналу: де зараз вигідно купити і де вигідно продати.

    Це коротке зведення з найсильнішими сигналами; повний прайс, усі сигнали
    й пошук артефакту — в меню бота (кнопку під постом додає викликач).

    Args:
        region: Код регіону
        current: Сьогоднішній зріз (market.build_snapshot)
        history: Попередні денні зрізи (без сьогоднішнього), найстаріший першим
        now: Поточний час
        catalog: Каталог — для назв (параметр — для тестів)

    Returns:
        Текст поста
    """
    names = {artifact.id: artifact.name for artifact in catalog}
    lines = [f"📊 Ринок артефактів {region} · {now.astimezone(LOCAL_TZ):%d.%m}"]

    if not history:
        lines += [
            "",
            "Перший зріз цін збережено. Сигнали «купити/продати» з'являться з наступного "
            "дайджесту — поки нема з чим порівнювати.",
            "",
            "Прайс усіх артефактів за рідкістю — в боті 👇",
        ]
        return "\n".join(lines)

    lines.append(baseline_note(history))
    buys, sells = signals(
        quotes(current, history, MARKET_MIN_LOTS), MARKET_SIGNAL_PCT, MARKET_MIN_LOTS
    )

    if not buys and not sells:
        lines += [
            "",
            f"😴 Ринок спокійний: жодна ціна не відійшла від звичної на {MARKET_SIGNAL_PCT:g}%+.",
        ]
    if buys:
        lines += ["", f"💰 Вигідно купити — дешевше, ніж зазвичай ({len(buys)})"]
        for quote in buys[:CHANNEL_SIGNALS_COUNT]:
            lines += signal_lines(quote, names)
    if sells:
        lines += ["", f"📤 Вигідно продати — дорожче, ніж зазвичай ({len(sells)})"]
        for quote in sells[:CHANNEL_SIGNALS_COUNT]:
            lines += signal_lines(quote, names)

    lines += [
        "",
        f"Сигнал — відхилення від звичної ціни на {MARKET_SIGNAL_PCT:g}%+, "
        f"пари з {MARKET_MIN_LOTS}+ лотами. Ціни — медіана виставлених лотів.",
        "Усі сигнали, прайс і пошук артефакту — в боті 👇",
    ]
    return "\n".join(lines)


def market_button(bot_username: Optional[str], region: str) -> Optional[Button]:
    """Кнопка під постом, що відкриває меню ринку в особистих повідомленнях бота."""
    if not bot_username:
        return None
    return (
        "📊 Відкрити аналітику",
        f"https://t.me/{bot_username}?start={DEEP_LINK_PREFIX}{region}",
    )


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
        self._username: Optional[str] = None
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

    async def _send(self, chat_id: str, text: str, button: Optional[Button] = None) -> None:
        """Відправити повідомлення в один чат.

        Args:
            chat_id: ID чату або юзернейм каналу
            text: Текст повідомлення
            button: Кнопка-посилання під повідомленням (текст, url) або None
        """
        assert self._client is not None
        payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if button:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
        resp = await self._client.post(f"/{TELEGRAM_SEND_METHOD}", json=payload)
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", TELEGRAM_RETRY_AFTER_DEFAULT)
            await asyncio.sleep(retry_after)
            await self._send(chat_id, text, button)
            return
        resp.raise_for_status()

    async def broadcast(self, text: str, button: Optional[Button] = None) -> None:
        """Розіслати повідомлення у всі чати.

        Args:
            text: Текст повідомлення
            button: Кнопка-посилання під повідомленням (текст, url) або None
        """
        if self._client is None:
            return
        for chat_id in self._chat_ids:
            try:
                await self._send(chat_id, text, button)
            except httpx.HTTPStatusError as exc:
                log.error("не змогла відправити в %s: %s", chat_id, exc)

    async def bot_username(self) -> Optional[str]:
        """Юзернейм бота (getMe) — для посилання на меню. None, якщо не вдалося."""
        if self._client is None:
            return None
        if self._username:
            return self._username
        try:
            resp = await self._client.get("/getMe")
            resp.raise_for_status()
            self._username = resp.json().get("result", {}).get("username")
            return self._username
        except (httpx.HTTPError, ValueError):
            log.warning("не вдалося дізнатися юзернейм бота — пост піде без кнопки", exc_info=True)
            return None

    async def aclose(self) -> None:
        """Закрити HTTP-клієнт."""
        if self._client is not None:
            await self._client.aclose()
