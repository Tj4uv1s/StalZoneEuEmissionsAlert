"""Ручний запуск звітів поза розкладом.

    python -m app.cli top      — топ лотів після ОСТАННЬОГО викиду
    python -m app.cli digest   — дайджест ринку зараз
    ... --region EU            — тільки один регіон (типово — усі з REGIONS)
    ... --dry-run              — тільки вивести в консоль, у канал не слати

Працює поруч із запущеним ботом: лок single_instance не бере і стан не
змінює — лише читає. Тому ручний дайджест порівнює з останнім ПЛАНОВИМ
зрізом і не перезаписує його: інакше плановий дайджест вирішив би, що
сьогодні вже був, і пропустив би свій час, а завтрашнє порівняння рахувалось
би не за добу.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import config
from app.notify import Notifier
from app.reports import MarketUnavailable, build_digest, build_top_report, last_emission_window
from app.stalcraft import StalcraftClient
from app.storage import JsonStorage

log = logging.getLogger(__name__)


class ReportError(RuntimeError):
    """Звіт неможливо зібрати (немає даних у API тощо)."""


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Розібрати аргументи командного рядка."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Ручний запуск звітів бота поза розкладом.",
    )
    parser.add_argument(
        "command",
        choices=("top", "digest"),
        help="top — топ лотів після останнього викиду; digest — дайджест ринку",
    )
    parser.add_argument("--region", help="тільки цей регіон (типово — усі з REGIONS)")
    parser.add_argument(
        "--dry-run", action="store_true", help="тільки вивести в консоль, у канал не слати"
    )
    return parser.parse_args(argv)


async def collect(
    command: str, region: str, api: StalcraftClient, storage: JsonStorage, now: datetime
) -> list[str]:
    """Зібрати повідомлення звіту. Порожній список — надсилати нічого.

    Raises:
        ReportError: API не віддав потрібних даних
        MarketUnavailable: обхід ринку здебільшого впав
    """
    if command == "top":
        window = await last_emission_window(api, region, now, config.top_lots_delay)
        if window is None:
            raise ReportError(f"{region}: API не віддав час кінця останнього викиду")
        text = await build_top_report(api, region, *window)
        return [text] if text else []

    market = storage.get(region).get("market") or {}
    digest = await build_digest(api, region, market, now)
    return digest.messages


def _console_utf8() -> None:
    """Консоль Windows (cp1251) падає на емодзі в print — перемикаємо на UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


async def main(argv: Optional[list[str]] = None, now: Optional[datetime] = None) -> int:
    """Точка входу. Повертає код виходу: 0 — усе гаразд, 1 — хоч один регіон не вдався."""
    args = parse_args(argv)
    _console_utf8()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    config.require()

    now = now or datetime.now(timezone.utc)
    regions = [args.region] if args.region else config.regions
    storage = JsonStorage(config.state_path)
    notifier = None if args.dry_run else Notifier(config.tg_token, config.tg_chat_ids)
    exit_code = 0

    try:
        async with StalcraftClient(
            config.api_base, config.api_client_id, config.api_client_secret
        ) as api:
            for region in regions:
                try:
                    messages = await collect(args.command, region, api, storage, now)
                    if not messages:
                        print(f"{region}: після викиду артефактів на аукціон не виставляли — нема що надсилати")
                        continue
                    for text in messages:
                        print(text, end="\n\n")
                        if notifier:
                            await notifier.broadcast(text)
                except (ReportError, MarketUnavailable, httpx.HTTPError) as exc:
                    log.error("%s: %s", region, exc)
                    exit_code = 1
    finally:
        if notifier:
            await notifier.aclose()

    if not args.dry_run and exit_code == 0:
        print("Надіслано в канал." if config.tg_token and config.tg_chat_ids else
              "TG_TOKEN/TG_CHAT_IDS не задані — тільки вивели в консоль.")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
