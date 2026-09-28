"""Екрани меню ринку в особистих повідомленнях бота: дані -> текст (HTML) + кнопки.

Чиста логіка без Telegram: екран — це View(text, keyboard), де кнопка — пара
(напис, callback_data). Обгортка aiogram у telegram_bot.py лише перетворює це
на InlineKeyboardMarkup і редагує повідомлення. Тому весь інтерфейс
покривається тестами без мережі.

Меню читає ЗБЕРЕЖЕНИЙ щоденний зріз зі стану, а не ринок наживо: живий обхід
— це ~30 с і ~100 запитів до API на кожне натискання.

callback_data (ліміт Telegram — 64 байти):
    m:EU        меню            b:EU:0    купити, сторінка 0
    s:EU:0      продати         p:EU:0    прайс, сторінка 0
    r:EU        вибір рідкості  q:EU:3:0  рідкість 3, сторінка 0
    f:EU        підказка пошуку a:EU:id   картка артефакту
    h:EU        довідка         noop      лічильник сторінок (нічого не робить)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Callable, Optional

from app.artifacts import CATALOG, Artifact
from app.constants import (
    ARTIFACT_QUALITY,
    MARKET_BASELINE_DAYS,
    MARKET_MIN_LOTS,
    MARKET_SIGNAL_PCT,
    UI_PAGE_SIZE,
    UI_SEARCH_MAX_RESULTS,
)
from app.formatting import (
    format_compact,
    format_pct,
    local_date_time,
    quality_badge,
    trend_arrow,
)
from app.market import Quote, is_solid, market_history, quotes, signals
from app.tracker import parse_iso

Keyboard = list[list[tuple[str, str]]]
NOOP = "noop"


@dataclass(frozen=True)
class View:
    """Один екран: HTML-текст і кнопки під ним."""

    text: str
    keyboard: Keyboard


@dataclass(frozen=True)
class MarketData:
    """Останній збережений зріз регіону разом зі звичними цінами."""

    region: str
    taken_at: datetime
    quotes: list[Quote]
    baseline_days: int  # скільки попередніх днів у базі (0 — перший зріз)

    @classmethod
    def from_state(cls, region: str, state: dict) -> Optional["MarketData"]:
        """Зібрати з стану регіону. None, якщо жодного зрізу ще немає."""
        history = market_history(state)
        if not history:
            return None
        current = history[-1]
        base = history[:-1][-MARKET_BASELINE_DAYS:]
        return cls(
            region=region,
            taken_at=parse_iso(current["taken_at"]),
            quotes=quotes(current["prices"], base, MARKET_MIN_LOTS),
            baseline_days=len(base),
        )

    def signals(self) -> tuple[list[Quote], list[Quote]]:
        """(купити, продати) — ті самі правила, що й у пості каналу."""
        return signals(self.quotes, MARKET_SIGNAL_PCT, MARKET_MIN_LOTS)


# --- дрібні будівельні блоки ----------------------------------------------

def _names(catalog: tuple[Artifact, ...]) -> dict[str, str]:
    return {artifact.id: artifact.name for artifact in catalog}


def _bold_title(name: str, quality: int) -> str:
    """'🔵 <b>Призма</b> · Особый' з екрануванням назви."""
    color, label = quality_badge(quality)
    title = f"{color} <b>{escape(name)}</b>"
    return f"{title} · {label}" if label else title


def _page(items: list, page: int) -> tuple[list, int, int]:
    """Зріз сторінки. Номер сторінки обрізається в допустимі межі."""
    pages = max(1, -(-len(items) // UI_PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    return items[page * UI_PAGE_SIZE : (page + 1) * UI_PAGE_SIZE], page, pages


def _nav(prefix: str, page: int, pages: int) -> list[list[tuple[str, str]]]:
    """Ряд ◀ 2/5 ▶ — тільки якщо сторінок більше однієї."""
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(("◀", f"{prefix}:{page - 1}"))
    row.append((f"{page + 1}/{pages}", NOOP))
    if page < pages - 1:
        row.append(("▶", f"{prefix}:{page + 1}"))
    return [row]


def _back(region: str) -> list[list[tuple[str, str]]]:
    return [[("⬅ Меню", f"m:{region}")]]


def _data_line(data: MarketData) -> str:
    """'Дані на 28.09, 20:00 · звична ціна — середня за 7 дн.'"""
    line = f"Дані на {local_date_time(data.taken_at)}"
    if data.baseline_days:
        return f"{line} · звична ціна — середня за {data.baseline_days} дн."
    return f"{line} · перший зріз, сигнали з'являться з наступного дня"


def verdict(quote: Quote) -> str:
    """Що робити з цією парою — людською мовою."""
    if not is_solid(quote, MARKET_MIN_LOTS):
        return "мало лотів — ціна орієнтовна"
    if quote.pct is None:
        return "звичної ціни ще немає"
    if quote.pct <= -MARKET_SIGNAL_PCT:
        return "💰 вигідно купити"
    if quote.pct >= MARKET_SIGNAL_PCT:
        return "📤 вигідно продати"
    return "≈ звична ціна"


# --- екрани -------------------------------------------------------------

def no_data(region: str) -> View:
    return View(
        f"<b>📊 Ринок артефактів {region}</b>\n\n"
        "Даних ще немає: перший зріз ринку з'явиться після щоденного дайджесту.",
        [],
    )


def menu(data: Optional[MarketData], region: str, regions: list[str]) -> View:
    """Головне меню ринку регіону."""
    region_row = (
        [[(f"{r} ✓" if r == region else r, f"m:{r}") for r in regions]] if len(regions) > 1 else []
    )
    if data is None:
        view = no_data(region)
        return View(view.text, region_row)

    buys, sells = data.signals()
    text = (
        f"<b>📊 Ринок артефактів {region}</b>\n"
        f"{_data_line(data)}\n\n"
        f"💰 Вигідно купити: <b>{len(buys)}</b>\n"
        f"📤 Вигідно продати: <b>{len(sells)}</b>\n\n"
        "Оберіть розділ 👇 або просто надішліть назву артефакту."
    )
    keyboard = [
        [(f"💰 Купити ({len(buys)})", f"b:{region}:0"), (f"📤 Продати ({len(sells)})", f"s:{region}:0")],
        [("📋 Прайс", f"p:{region}:0"), ("🎨 За рідкістю", f"r:{region}")],
        [("🔍 Пошук артефакту", f"f:{region}"), ("❓ Довідка", f"h:{region}")],
        *region_row,
    ]
    return View(text, keyboard)


def signal_list(
    data: MarketData, side: str, page: int, catalog: tuple[Artifact, ...] = CATALOG
) -> View:
    """Усі сигнали купити ('b') або продати ('s'), посторінково."""
    buys, sells = data.signals()
    items = buys if side == "b" else sells
    if side == "b":
        title = "💰 Вигідно купити — дешевше, ніж зазвичай"
    else:
        title = "📤 Вигідно продати — дорожче, ніж зазвичай"
    header = f"<b>{title}</b>\n{_data_line(data)}"

    if not items:
        empty = (
            "Сигналів ще немає: звична ціна з'явиться з наступного дня."
            if not data.baseline_days
            else f"Зараз немає цін, що відійшли від звичних на {MARKET_SIGNAL_PCT:g}%+."
        )
        return View(f"{header}\n\n{empty}", _back(data.region))

    names = _names(catalog)
    chunk, page, pages = _page(items, page)
    lines = []
    for index, quote in enumerate(chunk, start=page * UI_PAGE_SIZE + 1):
        name = names.get(quote.artifact_id, quote.artifact_id)
        lines.append(
            f"{index}. {_bold_title(name, quote.quality)}  <b>{format_pct(quote.pct)}</b>\n"
            f"    {format_compact(quote.price)} · зазвичай {format_compact(quote.baseline)}"
            f" · лотів: {quote.count}"
        )
    return View(
        header + "\n\n" + "\n".join(lines),
        _nav(f"{side}:{data.region}", page, pages) + _back(data.region),
    )


def price_list(data: MarketData, page: int, catalog: tuple[Artifact, ...] = CATALOG) -> View:
    """Прайс: рядок на артефакт, у ньому медіани всіх рідкостей на ринку."""
    by_artifact: dict[str, dict[int, Quote]] = {}
    for quote in data.quotes:
        by_artifact.setdefault(quote.artifact_id, {})[quote.quality] = quote

    rows = []
    for artifact in sorted(catalog, key=lambda a: a.name):
        entries = by_artifact.get(artifact.id)
        if not entries:
            continue
        cells = " · ".join(
            f"{quality_badge(q)[0]} {format_compact(entries[q].price)}"
            f"{'' if is_solid(entries[q], MARKET_MIN_LOTS) else '*'}"
            for q in sorted(entries)
        )
        rows.append(f"<b>{escape(artifact.name)}</b> — {cells}")

    header = f"<b>📋 Прайс</b> — медіана виставлених лотів\n{_data_line(data)}"
    if not rows:
        return View(f"{header}\n\nНа аукціоні не було артефактів.", _back(data.region))
    chunk, page, pages = _page(rows, page)
    footer = f"\n\n* менше {MARKET_MIN_LOTS} лотів — ціна орієнтовна"
    return View(
        header + "\n\n" + "\n".join(chunk) + footer,
        _nav(f"p:{data.region}", page, pages) + _back(data.region),
    )


def rarity_picker(data: MarketData) -> View:
    """Вибір рідкості: кнопка на кожну, з кількістю артефактів на ринку."""
    counts = {q: 0 for q in ARTIFACT_QUALITY}
    for quote in data.quotes:
        if quote.quality in counts:
            counts[quote.quality] += 1
    buttons = [
        (f"{color} {label} ({counts[q]})", f"q:{data.region}:{q}:0")
        for q, (color, label) in ARTIFACT_QUALITY.items()
    ]
    keyboard = [buttons[i : i + 2] for i in range(0, len(buttons), 2)] + _back(data.region)
    return View(
        f"<b>🎨 За рідкістю</b>\n{_data_line(data)}\n\nОберіть рідкість 👇",
        keyboard,
    )


def rarity_list(
    data: MarketData, quality: int, page: int, catalog: tuple[Artifact, ...] = CATALOG
) -> View:
    """Усі артефакти однієї рідкості, від найдорожчого, з рухом ціни."""
    color, label = quality_badge(quality)
    names = _names(catalog)
    items = sorted(
        (q for q in data.quotes if q.quality == quality), key=lambda q: q.price, reverse=True
    )
    header = f"<b>{color} {label}</b> — від найдорожчого\n{_data_line(data)}"
    back = [[("⬅ Рідкості", f"r:{data.region}"), ("⬅ Меню", f"m:{data.region}")]]
    if not items:
        return View(f"{header}\n\nАртефактів цієї рідкості на ринку не було.", back)

    chunk, page, pages = _page(items, page)
    lines = []
    for quote in chunk:
        name = escape(names.get(quote.artifact_id, quote.artifact_id))
        price = format_compact(quote.price)
        if not is_solid(quote, MARKET_MIN_LOTS):
            lines.append(f"<b>{name}</b> — {price}*")
        elif quote.pct is None:
            lines.append(f"<b>{name}</b> — {price}")
        else:
            lines.append(f"<b>{name}</b> — {price}  {trend_arrow(quote.pct)} {format_pct(quote.pct)}")
    footer = f"\n\n* менше {MARKET_MIN_LOTS} лотів — ціна орієнтовна"
    return View(
        header + "\n\n" + "\n".join(lines) + footer,
        _nav(f"q:{data.region}:{quality}", page, pages) + back,
    )


def help_view(region: str) -> View:
    """Довідка: що вміє бот і як читати сигнали."""
    return View(
        "<b>📊 Бот ринку артефактів STALCRAFT</b>\n\n"
        "<b>Що вміє</b>\n"
        "• /market — меню ринку: що вигідно купити й продати, прайс, рідкості\n"
        "• Надішліть назву артефакту (можна частину, наприклад <i>призма</i>) — "
        "картка з цінами за всіма рідкостями\n"
        "• /help — ця довідка\n\n"
        "<b>Як читати сигнали</b>\n"
        f"💰 вигідно купити — зараз дешевше звичної ціни на {MARKET_SIGNAL_PCT:g}%+\n"
        f"📤 вигідно продати — зараз дорожче звичної ціни на {MARKET_SIGNAL_PCT:g}%+\n"
        f"Звична ціна — середня за {MARKET_BASELINE_DAYS} днів. Ціни — медіана "
        "виставлених на аукціон лотів; оновлюються щодня з дайджестом.\n\n"
        "Сповіщення про викиди й топ лотів після викиду — у каналі.",
        [[("📊 Відкрити меню ринку", f"m:{region}")]],
    )


def search_prompt(region: str) -> View:
    return View(
        "<b>🔍 Пошук артефакту</b>\n\n"
        "Надішліть назву або її частину — наприклад, <i>приз</i> або <i>ежик</i>.",
        _back(region),
    )


def artifact_card(
    data: Optional[MarketData], artifact: Artifact, region: str
) -> View:
    """Картка артефакту: кожна рідкість — ціна, звична ціна, рух і що робити."""
    header = f"<b>🔎 {escape(artifact.name)}</b>"
    if data is None:
        return View(f"{header}\n\n{no_data(region).text}", _back(region))

    entries = sorted(
        (q for q in data.quotes if q.artifact_id == artifact.id), key=lambda q: q.quality
    )
    if not entries:
        return View(
            f"{header}\n{_data_line(data)}\n\nНа момент зрізу цього артефакту на аукціоні не було.",
            _back(region),
        )

    blocks = []
    for quote in entries:
        color, label = quality_badge(quote.quality)
        first = f"{color} <b>{label or '—'}</b> — {format_compact(quote.price)}"
        if quote.pct is not None and is_solid(quote, MARKET_MIN_LOTS):
            first += f"  {trend_arrow(quote.pct)} {format_pct(quote.pct)}"
        details = [f"лотів: {quote.count}"]
        if quote.baseline:
            details.insert(0, f"зазвичай {format_compact(quote.baseline)}")
        blocks.append(f"{first}\n    {' · '.join(details)} · {verdict(quote)}")
    return View(f"{header}\n{_data_line(data)}\n\n" + "\n\n".join(blocks), _back(region))


def _normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е")


def search(
    data: Optional[MarketData],
    query: str,
    region: str,
    catalog: tuple[Artifact, ...] = CATALOG,
) -> View:
    """Пошук артефакту за частиною назви (без регістру, ё = е).

    Точний збіг — одразу картка; кілька — кнопки на вибір; забагато — просимо
    уточнити.
    """
    needle = _normalize(query)
    if len(needle) < 2:
        return View("Напишіть хоча б 2 літери назви артефакту.", _back(region))

    matches = [a for a in catalog if needle in _normalize(a.name)]
    exact = [a for a in matches if _normalize(a.name) == needle]
    if len(exact) == 1 or len(matches) == 1:
        return artifact_card(data, (exact or matches)[0], region)
    if not matches:
        return View(f"Не знайшов артефакт «{escape(query.strip())}». Спробуйте іншу частину назви.", _back(region))
    if len(matches) > UI_SEARCH_MAX_RESULTS:
        return View(
            f"Знайшов {len(matches)} артефактів з «{escape(query.strip())}» — уточніть назву.",
            _back(region),
        )
    matches.sort(key=lambda a: a.name)
    keyboard = [[(a.name, f"a:{region}:{a.id}")] for a in matches] + _back(region)
    return View(f"Знайдено {len(matches)} — оберіть 👇", keyboard)


# --- маршрутизація натискань ------------------------------------------------

def render_callback(
    callback_data: str,
    get_state: Callable[[str], dict],
    regions: list[str],
    catalog: tuple[Artifact, ...] = CATALOG,
) -> Optional[View]:
    """Екран для натиснутої кнопки. None — кнопку ігноруємо (noop, чужий регіон, сміття).

    Регіон перевіряється за списком: callback_data приходить від клієнта, і
    підставити туди можна що завгодно.
    """
    parts = callback_data.split(":")
    if len(parts) < 2 or parts[1] not in regions:
        return None
    action, region, args = parts[0], parts[1], parts[2:]
    data = MarketData.from_state(region, get_state(region))

    try:
        if action == "m":
            return menu(data, region, regions)
        if action == "f":
            return search_prompt(region)
        if action == "h":
            return help_view(region)
        if action == "a" and args:
            artifact = next((a for a in catalog if a.id == args[0]), None)
            return artifact_card(data, artifact, region) if artifact else None
        if data is None:
            return no_data(region)
        if action in ("b", "s") and args:
            return signal_list(data, action, int(args[0]), catalog)
        if action == "p" and args:
            return price_list(data, int(args[0]), catalog)
        if action == "r":
            return rarity_picker(data)
        if action == "q" and len(args) >= 2:
            return rarity_list(data, int(args[0]), int(args[1]), catalog)
    except ValueError:
        return None  # не число там, де очікували номер сторінки чи рідкості
    return None
