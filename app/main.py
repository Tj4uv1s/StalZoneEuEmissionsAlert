"""Точка входа: бесконечный цикл опроса API и рассылки уведомлений."""
from __future__ import annotations

import asyncio

from app.config import config
from app.notify import Notifier, format_event
from app.stalcraft import StalcraftClient
from app.storage import JsonStorage
from app.tracker import diff


async def poll_once(api: StalcraftClient, storage: JsonStorage, notifier: Notifier) -> None:
    for region in config.regions:
        try:
            response = await api.get_emission(region)
        except Exception as exc:  # noqa: BLE001 — на верхнем уровне логируем всё
            print(f"[poll] {region}: ошибка запроса к API: {exc}")
            continue

        events, new_state = diff(region, storage.get(region), response)
        for event in events:
            text = format_event(event)
            print(f"[poll] {region}: {text!r}")
            await notifier.broadcast(text)
        if new_state != storage.get(region):
            storage.set(region, new_state)


async def main() -> None:
    config.require()
    storage = JsonStorage(config.state_path)
    notifier = Notifier(config.tg_token, config.tg_chat_ids)
    async with StalcraftClient(config.api_base, config.api_token) as api:
        print(
            f"Старт. Регионы: {', '.join(config.regions)}; "
            f"интервал: {config.poll_interval}s; API: {config.api_base}"
        )
        try:
            while True:
                await poll_once(api, storage, notifier)
                await asyncio.sleep(config.poll_interval)
        finally:
            await notifier.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено.")
