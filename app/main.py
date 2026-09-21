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

from app.config import config
from app.constants import RATE_LIMIT_SAFETY_MARGIN
from app.notify import Notifier, format_event
from app.stalcraft import RateLimit, StalcraftClient
from app.storage import JsonStorage
from app.tracker import diff

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


async def poll_once(api: StalcraftClient, storage: JsonStorage, notifier: Notifier) -> float:
    """Опитати все регіони, розіслати подій. Повернути паузу до наступного циклу.

    Args:
        api: Клієнт API
        storage: Сховище стану
        notifier: Відправник повідомлень

    Returns:
        Рекомендована пауза до наступного опитування в секундах
    """
    delay = config.min_poll_interval
    for region in config.regions:
        try:
            response, rate = await api.get_emission(region)
        except Exception:
            log.exception("%s: помилка запиту до API", region)
            continue

        log.info("%s: викид — %s", region, "ЙДЕ" if response.get("currentStart") else "немає")

        events, new_state = diff(region, storage.get(region), response)
        for event in events:
            text = format_event(event)
            log.info("%s: подія -> %r", region, text)
            await notifier.broadcast(text)
        if new_state != storage.get(region):
            storage.set(region, new_state)

        delay = max(delay, _next_delay(rate, config.min_poll_interval))

    return delay


async def main() -> None:
    """Головна функція."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config.require()
    storage = JsonStorage(config.state_path)
    notifier = Notifier(config.tg_token, config.tg_chat_ids)
    async with StalcraftClient(
        config.api_base, config.api_client_id, config.api_client_secret
    ) as api:
        log.info(
            "Старт. Регіони: %s; пол інтервалу: %ss; API: %s",
            ", ".join(config.regions),
            config.min_poll_interval,
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
    except KeyboardInterrupt:
        print("\nЗупинено.")
