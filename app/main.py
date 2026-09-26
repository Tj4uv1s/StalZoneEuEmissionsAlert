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

from app.auction import top_sales_since
from app.config import config
from app.constants import RATE_LIMIT_SAFETY_MARGIN
from app.notify import Notifier, format_event, format_top_sales
from app.stalcraft import RateLimit, StalcraftClient
from app.single_instance import AlreadyRunning, SingleInstance
from app.storage import JsonStorage
from app.tracker import ENDED, diff, parse_iso, to_iso

log = logging.getLogger(__name__)


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


async def _handle_top_sales(
    region: str, state: dict, notifier: Notifier, api: StalcraftClient, now: datetime
) -> None:
    """Якщо надійшов час забирати топ продажів для region — забрати й розіслати.

    Мутує `state` на місці (прибирає "pending_top_sales", коли обробили).
    Виклик top_sales_since може забрати десятки секунд (перебирає весь
    каталог артефактів) — і саме тому робимо це рідко, раз на викид, а не
    на кожному опитуванні.

    Args:
        region: Код регіону
        state: Стан регіону (той самий dict, що піде в storage.set)
        notifier: Відправник повідомлень
        api: Клієнт API
        now: Поточний час
    """
    pending = state.get("pending_top_sales")
    if not pending:
        return

    due = parse_iso(pending.get("due"))
    since = parse_iso(pending.get("since"))
    if due is None or since is None:
        # Кривий запис у стані (наприклад, ручне редагування) — не зациклюємось
        state.pop("pending_top_sales", None)
        return
    if now < due:
        return

    sales = await top_sales_since(api, region, since)
    text = format_top_sales(region, sales, since, now)
    if text:
        log.info("%s: топ продажів -> %r", region, text)
        await notifier.broadcast(text)
    else:
        log.info("%s: за викид ніхто не продав артефактів — мовчимо", region)
    state.pop("pending_top_sales", None)


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
                    due = parse_iso(event.at) + timedelta(seconds=config.top_sales_delay)
                    new_state["pending_top_sales"] = {"since": event.at, "due": to_iso(due)}

            await _handle_top_sales(region, new_state, notifier, api, now)

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
            "топ продажів через %s хв після викиду; API: %s",
            ", ".join(config.regions),
            config.min_poll_interval,
            int(config.announce_max_age // 60),
            int(config.top_sales_delay // 60),
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
