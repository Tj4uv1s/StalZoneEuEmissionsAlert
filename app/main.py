"""Точка входу: цикл опитування API та розсилання сповіщень.

Опитуємо "максимально часто, наскільки можна" — але не навмисно: підстроюємо
паузу під реальні заголовки X-RateLimit-* від сервера, з полом min_poll_interval
й запасом (RATE_LIMIT_SAFETY_MARGIN), щоб не словити 429 й не бути невіжливими
до demo-Client-Id, який загальний на всіх, хто зараз тестує API.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app.auction import fetch_market, top_listings_between
from app.config import config
from app.constants import (
    LOCAL_TZ_LABEL,
    LOCAL_TZ_NAME,
    MARKET_RETRY_DELAY,
    RATE_LIMIT_SAFETY_MARGIN,
)
from app.market import build_snapshot, digest_due
from app.notify import Notifier, format_event, format_market_digest, format_top_listings
from app.stalcraft import RateLimit, StalcraftClient
from app.single_instance import AlreadyRunning, SingleInstance
from app.storage import JsonStorage
from app.tracker import ENDED, diff, parse_iso, to_iso

log = logging.getLogger(__name__)

_LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)


def _next_delay(rate: RateLimit, floor: float) -> float:
    """Визначити, скільки чекати до наступного опитування регіону.

    Коли запасу багато — жимемо з полом floor. Коли залишок ліміту починає
    наближатися до кінця вікна — розмазуємо решту запитів на решту часу до
    reset, щоб не упиратися в 429 раніше часу.

    Args:
        rate: Інформація про rate limit з відповіді
        floor: Мінімальна пауза в секундах

    Returns:
        Рекомендована пауза в секундах
    """
    if rate.remaining is None or rate.limit is None or rate.reset_at is None:
        return floor

    budget = max(1, int(rate.limit * RATE_LIMIT_SAFETY_MARGIN))
    if rate.remaining >= budget:
        return floor

    window_left = max(0.0, rate.reset_at - time.time())
    if rate.remaining <= 0:
        return max(floor, window_left)
    return max(floor, window_left / rate.remaining)


async def _send_once(
    region: str, notifier: Notifier, messages: list[str], what: str
) -> None:
    """Розіслати результат важкого збору рівно один раз, без повторів.

    Збір топу чи дайджесту — це обхід усього каталогу (~100 запитів, ~20 с).
    Якби мережева помилка Telegram вилітала звідси, poll_once не зберіг би
    стан, і вже за 2 с бот знову обходив би весь ринок — і так по колу, поки
    Telegram лежить. Тому викликач спершу фіксує стан, а збій відправки тут
    тільки логується: краще втратити один дайджест, ніж влаштувати шторм
    запитів до API гри. (Сповіщення про викид так НЕ роблять: там повтор
    відправки на наступному опитуванні — бажана поведінка.)
    """
    try:
        for text in messages:
            log.info("%s: %s -> %r", region, what, text)
            await notifier.broadcast(text)
    except httpx.HTTPError:
        log.exception("%s: не вдалося розіслати %s — повторювати не будемо", region, what)


async def _handle_top_listings(
    region: str, state: dict, notifier: Notifier, api: StalcraftClient, now: datetime
) -> None:
    """Якщо надійшов час — зібрати й розіслати топ виставлених на аукціон артефактів.

    Мутує `state` на місці (прибирає "pending_top_lots", коли обробили).
    Збір перебирає весь каталог артефактів і може забрати десятки секунд —
    тому робимо це рідко, раз на викид, а не на кожному опитуванні.

    Вікно — рівно [кінець викиду, due], а не до `now`: якщо бот перевірив
    трохи пізніше (пауза опитування, рестарт), вікно від цього не розповзається.

    Args:
        region: Код регіону
        state: Стан регіону (той самий dict, що піде в storage.set)
        notifier: Відправник повідомлень
        api: Клієнт API
        now: Поточний час
    """
    pending = state.get("pending_top_lots")
    if not pending:
        return

    due = parse_iso(pending.get("due"))
    since = parse_iso(pending.get("since"))
    if due is None or since is None:
        # Кривий запис у стані (наприклад, ручне редагування) — не зациклюємось
        state.pop("pending_top_lots", None)
        return
    if now < due:
        return

    listings = await top_listings_between(api, region, since, due)
    text = format_top_listings(region, listings, since, due)
    state.pop("pending_top_lots", None)  # спершу знімаємо задачу, потім шлемо — див. _send_once
    if text:
        await _send_once(region, notifier, [text], "топ лотів")
    else:
        log.info("%s: після викиду артефактів на аукціон не виставляли — мовчимо", region)


async def _handle_market_digest(
    region: str, state: dict, notifier: Notifier, api: StalcraftClient, now: datetime
) -> None:
    """Раз на добу — зріз цін ринку, порівняння з учорашнім і розсилка дайджесту.

    Мутує `state` на місці: кладе новий зріз у "market" (старий замінюється —
    для порівняння потрібен лише попередній день).

    Якщо обхід ринку здебільшого впав (мережа, API лежить), зріз НЕ
    зберігаємо: він був би майже порожнім і "стер" би вчорашні ціни, а
    завтрашнє порівняння вийшло б ні з чим. Замість цього — нова спроба
    через MARKET_RETRY_DELAY, а не на кожному опитуванні.

    Args:
        region: Код регіону
        state: Стан регіону (той самий dict, що піде в storage.set)
        notifier: Відправник повідомлень
        api: Клієнт API
        now: Поточний час
    """
    market = state.get("market") or {}
    if not digest_due(
        market.get("taken_at"),
        now,
        config.market_digest_hour,
        _LOCAL_TZ,
        state.get("market_retry_at"),
    ):
        return

    fetched = await fetch_market(api, region)
    if fetched.failed * 2 > fetched.total:
        log.warning(
            "%s: обхід ринку впав для %s з %s артефактів — зріз не зберігаю, спробую пізніше",
            region,
            fetched.failed,
            fetched.total,
        )
        state["market_retry_at"] = to_iso(now + timedelta(seconds=MARKET_RETRY_DELAY))
        return

    current = build_snapshot(fetched.lots)
    messages = format_market_digest(
        region, current, market.get("prices"), market.get("taken_at"), now
    )
    # Спершу зберігаємо зріз, потім шлемо — див. _send_once
    state["market"] = {"taken_at": to_iso(now), "prices": current}
    state.pop("market_retry_at", None)
    await _send_once(region, notifier, messages, "дайджест ринку")


async def poll_once(
    api: StalcraftClient, storage: JsonStorage, notifier: Notifier, now: datetime | None = None
) -> float:
    """Опитати все регіони, розіслати подій. Повернути паузу до наступного циклу.

    Args:
        api: Клієнт API
        storage: Сховище стану
        notifier: Відправник повідомлень
        now: Поточний час. None (типово) — взяти реальний; параметр існує заради тестів,
             як і в tracker.diff.

    Returns:
        Рекомендована пауза до наступного опитування в секундах
    """
    delay = config.min_poll_interval
    now = now or datetime.now(timezone.utc)
    for region in config.regions:
        try:
            response, rate = await api.get_emission(region)
        except Exception:
            log.exception("%s: помилка запиту до API", region)
            continue

        log.info("%s: викид — %s", region, "ЙДЕ" if response.get("currentStart") else "немає")

        try:
            state = storage.get(region)
            events, new_state = diff(region, state, response, now, config.announce_max_age)
            for event in events:
                text = format_event(event)
                log.info("%s: подія -> %r", region, text)
                await notifier.broadcast(text)
                if event.kind == ENDED:
                    due = parse_iso(event.at) + timedelta(seconds=config.top_lots_delay)
                    new_state["pending_top_lots"] = {"since": event.at, "due": to_iso(due)}

            await _handle_top_listings(region, new_state, notifier, api, now)
            await _handle_market_digest(region, new_state, notifier, api, now)

            if new_state != state:
                storage.set(region, new_state)
        except Exception:
            # Один кривий регіон не має валити цикл по всіх інших
            log.exception("%s: помилка обробки події", region)

        delay = max(delay, _next_delay(rate, config.min_poll_interval))

    return delay


async def main() -> None:
    """Головна функція."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config.require()
    with SingleInstance(config.lock_path):
        await _run()


async def _run() -> None:
    """Цикл опитування. Викликається, коли ми вже єдиний екземпляр."""
    storage = JsonStorage(config.state_path)
    notifier = Notifier(config.tg_token, config.tg_chat_ids)
    async with StalcraftClient(
        config.api_base, config.api_client_id, config.api_client_secret
    ) as api:
        log.info(
            "Старт. Регіони: %s; пол інтервалу: %ss; анонсуємо події не старше %s хв; "
            "топ лотів аукціону через %s хв після викиду; дайджест ринку щодня з %s:00 %s; "
            "API: %s",
            ", ".join(config.regions),
            config.min_poll_interval,
            int(config.announce_max_age // 60),
            int(config.top_lots_delay // 60),
            config.market_digest_hour,
            LOCAL_TZ_LABEL,
            config.api_base,
        )
        try:
            while True:
                delay = await poll_once(api, storage, notifier)
                await asyncio.sleep(delay)
        finally:
            await notifier.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AlreadyRunning as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("\nЗупинено.")
