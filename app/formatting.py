"""Спільне форматування для повідомлень каналу й меню в особистих повідомленнях."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.constants import ARTIFACT_QUALITY, LOCAL_TZ_NAME, UNKNOWN_QUALITY

LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)

_COMPACT_UNITS = ((1_000_000_000, "млрд"), (1_000_000, "млн"), (1_000, "тис"))


def clock(moment: datetime) -> str:
    """Київський час як `HH:MM`."""
    return moment.astimezone(LOCAL_TZ).strftime("%H:%M")


def local_date_time(moment: datetime) -> str:
    """Київські дата й час як `28.09, 20:00`."""
    return moment.astimezone(LOCAL_TZ).strftime("%d.%m, %H:%M")


def format_price(price: int) -> str:
    """Ціна з розділювачем розрядів: 1500000 -> '1 500 000'."""
    return f"{price:,}".replace(",", " ")


def format_compact(value: int) -> str:
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


def format_pct(pct: float) -> str:
    """Відсоток зі знаком, без зайвої точності: +23% / −8%; малі — з десятими: +0,4%."""
    sign = "+" if pct >= 0 else "−"
    magnitude = abs(pct)
    text = f"{magnitude:.1f}".replace(".", ",") if magnitude < 10 else f"{magnitude:.0f}"
    return f"{sign}{text.removesuffix(',0')}%"


def trend_arrow(pct: float) -> str:
    """Стрілка як у крипто-тікерах: зелена вгору, червона вниз."""
    return "🟢▲" if pct > 0 else "🔴▼" if pct < 0 else "⚪"


def quality_badge(quality: int) -> tuple[str, str]:
    """(колір, назва) рідкості; для невідомої — знак питання без назви."""
    return ARTIFACT_QUALITY.get(quality, UNKNOWN_QUALITY)


def quality_title(name: str, quality: int) -> str:
    """'🔵 Призма · Особый' — назва артефакту з кольором і назвою рідкості."""
    color, label = quality_badge(quality)
    return f"{color} {name} · {label}" if label else f"{color} {name}"


def telegram_len(text: str) -> int:
    """Довжина так, як її рахує Telegram — в одиницях UTF-16 (емодзі = 2)."""
    return len(text.encode("utf-16-le")) // 2
