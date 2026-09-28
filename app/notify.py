"""Відправлення повідомлень у Telegram прямо через Bot API (без фреймворку).

Фреймворк (aiogram) додамо пізніше, коли з'являться команди від користувачів.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from zoneinfo import ZoneInfo

from app.artifacts import CATALOG, Artifact
from app.auction import Listing
from app.constants import (
    ARTIFACT_QUALITY,
    LOCAL_TZ_LABEL,
    LOCAL_TZ_NAME,
    MARKET_MIN_CHANGE_PCT,
    MARKET_MIN_LOTS,
    TELEGRAM_API_BASE,
    TELEGRAM_MESSAGE_LIMIT,
    TELEGRAM_RETRY_AFTER_DEFAULT,
    TELEGRAM_SEND_METHOD,
    TOP_MOVERS_COUNT,
    UNKNOWN_QUALITY,
)
from app.http import default_ssl_context
from app.market import PriceChange, compare, split_key, top_movers
from app.tracker import ENDED, WARNING, Event, parse_iso

log = logging.getLogger(__name__)

_LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)

_TEXT = {
    WARNING: "⚠️ Викид у регіоні {region}!",
    ENDED: "✅ Викид у регіоні {region} завершився.",
}

_MEDALS = ("🥇", "🥈", "🥉")


def _clock(moment: datetime) -> str:
    """Київський час як `HH:MM`, без підпису."""
    return moment.astimezone(_LOCAL_TZ).strftime("%H:%M")


def _format_time(iso_time: str | None) -> str | None:
    """Відформатувати ISO-8601 час як `HH:MM за Києвом`. None, якщо непридатний."""
    moment = parse_iso(iso_time)
    return f"{_clock(moment)} {LOCAL_TZ_LABEL}" if moment else None


def _format_price(price: int) -> str:
    """Відформатувати ціну з розділювачем розрядів: 1500000 -> '1 500 000'."""
    return f"{price:,}".replace(",", " ")


_COMPACT_UNITS = ((1_000_000_000, "млрд"), (1_000_000, "млн"), (1_000, "тис"))


def _format_compact(value: int) -> str:
    """Коротка ціна для щільних списків: 6500 -> '6,5 тис', 47000000 -> '47 млн'.

    Поріг одиниці трохи нижче за круглий (×0.9995), щоб 999 950 ставало
    '1 млн', а не '1000 тис'.
    """
    for threshold, unit in _COMPACT_UNITS:
        if value >= threshold * 0.9995:
            scaled = value / threshold
            text = f"{scaled:.1f}" if scaled < 9.95 else f"{scaled:.0f}"
            return f"{text.removesuffix('.0').replace('.', ',')} {unit}"
    return str(value)


def _format_pct(pct: float) -> str:
    """Відсоток зі знаком і комою: +23,4% / −8,1%."""
    sign = "+" if pct >= 0 else "−"
    return f"{sign}{abs(pct):.1f}%".replace(".", ",")


def _telegram_len(text: str) -> int:
    """Довжина так, як її рахує Telegram — в одиницях UTF-16 (емодзі = 2)."""
    return len(text.encode("utf-16-le")) // 2


def _split_message(lines: list[str], limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Розбити рядки на повідомлення, кожне не довше ліміту Telegram.

    Ріжемо тільки між рядками, щоб жоден запис не розірвався навпіл.
    """
    messages: list[str] = []
    current: list[str] = []
    for line in lines:
        if current and _telegram_len("\n".join([*current, line])) > limit:
            messages.append("\n".join(current).strip("\n"))
            current = []
        current.append(line)
    if current:
        messages.append("\n".join(current).strip("\n"))
    return messages


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
        f"🕒 {_clock(since)}–{_clock(until)} {LOCAL_TZ_LABEL}",
        "",
    ]
    for index, listing in enumerate(listings):
        rank = _MEDALS[index] if index < len(_MEDALS) else f"{index + 1}."
        color, label = ARTIFACT_QUALITY.get(listing.quality, UNKNOWN_QUALITY)
        name = f"{listing.artifact.name} · {label}" if label else listing.artifact.name
        lines.append(f"{rank} {color} {name} — {_format_price(listing.price)}")

    return "\n".join(lines)


def _quality_badge(quality: int) -> tuple[str, str]:
    """(колір, назва) рідкості; для невідомої — знак питання без назви."""
    return ARTIFACT_QUALITY.get(quality, UNKNOWN_QUALITY)


def _price_list_lines(current: dict, names: dict[str, str], order: list[str]) -> list[str]:
    """Прайс: рядок на артефакт, у ньому медіани всіх рідкостей, що є на ринку.

    Ціни тонкого ринку (менше MARKET_MIN_LOTS лотів) позначаємо зірочкою:
    на живих даних такі пари давали нісенітницю на кшталт "рідший дешевший
    за звичайніший" — це випадкова ціна одного-двох лотів, а не ринкова.
    """
    by_artifact: dict[str, dict[int, dict]] = {}
    for key, entry in current.items():
        artifact_id, quality = split_key(key)
        by_artifact.setdefault(artifact_id, {})[quality] = entry

    lines = []
    has_thin = False
    for artifact_id in order:
        entries = by_artifact.get(artifact_id)
        if not entries:
            continue  # артефакту зараз немає на ринку
        cells = []
        for quality in sorted(entries):
            thin = entries[quality]["count"] < MARKET_MIN_LOTS
            has_thin = has_thin or thin
            mark = "*" if thin else ""
            cells.append(
                f"{_quality_badge(quality)[0]} {_format_compact(entries[quality]['median'])}{mark}"
            )
        lines.append(f"{names.get(artifact_id, artifact_id)} — {' · '.join(cells)}")

    if has_thin:
        lines += ["", f"* менше {MARKET_MIN_LOTS} лотів — ціна орієнтовна"]
    return lines


def _mover_line(change: PriceChange, names: dict[str, str]) -> str:
    """Рядок тікера: 🟢▲ +23,4%  🟡 Жвачка · Легендарный  38 млн → 47 млн."""
    arrow = "🟢▲" if change.pct > 0 else "🔴▼"
    color, label = _quality_badge(change.quality)
    name = names.get(change.artifact_id, change.artifact_id)
    title = f"{name} · {label}" if label else name
    return (
        f"{arrow} {_format_pct(change.pct)}  {color} {title}  "
        f"{_format_compact(change.old)} → {_format_compact(change.new)}"
    )


def format_market_digest(
    region: str,
    current: dict,
    previous: dict | None,
    previous_taken_at: str | None,
    now: datetime,
    catalog: tuple[Artifact, ...] = CATALOG,
) -> list[str]:
    """Щоденний дайджест ринку артефактів — одне або кілька повідомлень.

    Без попереднього зрізу — прайс (медіана кожної рідкості), бо порівнювати
    ще нема з чим. З ним — тікер як у крипті: топ подорожчалих і подешевшалих.
    Прайс на весь каталог не влазить у ліміт Telegram (4096), тому результат —
    список повідомлень.

    Args:
        region: Код регіону
        current: Сьогоднішній зріз (market.build_snapshot)
        previous: Попередній зріз або None, якщо це перший
        previous_taken_at: Коли знімали попередній зріз (ISO) або None
        now: Поточний час
        catalog: Каталог — для назв і порядку (параметр — для тестів)

    Returns:
        Повідомлення для відправки по черзі
    """
    names = {artifact.id: artifact.name for artifact in catalog}
    order = [artifact.id for artifact in sorted(catalog, key=lambda a: a.name)]
    header = [f"📊 Ринок артефактів {region} · {now.astimezone(_LOCAL_TZ):%d.%m}"]

    if previous is None:
        header += [
            "Перший зріз цін — динаміка з'явиться в наступному дайджесті.",
            "Медіанна ціна виставлених лотів за рідкістю:",
            "",
        ]
        body = _price_list_lines(current, names, order)
        if not body:
            body = ["На аукціоні зараз немає артефактів."]
        return _split_message(header + body)

    before = parse_iso(previous_taken_at)
    since = f" проти {before.astimezone(_LOCAL_TZ):%d.%m, %H:%M}" if before else ""
    header.append(f"Медіанна ціна виставлених лотів{since}")

    gainers, losers = top_movers(
        compare(previous, current, MARKET_MIN_LOTS), TOP_MOVERS_COUNT, MARKET_MIN_CHANGE_PCT
    )
    if not gainers and not losers:
        return _split_message(header + ["", "Суттєвих змін цін немає."])

    body: list[str] = []
    if gainers:
        body += ["", "🟢 Дорожчають"] + [_mover_line(c, names) for c in gainers]
    if losers:
        body += ["", "🔴 Дешевшають"] + [_mover_line(c, names) for c in losers]
    body += [
        "",
        f"Враховано пари, де лотів було від {MARKET_MIN_LOTS} в обидва дні, "
        f"і зміни від {MARKET_MIN_CHANGE_PCT:g}%.",
    ]
    return _split_message(header + body)


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
