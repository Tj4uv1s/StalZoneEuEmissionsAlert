"""Інтерактивне меню ринку в особистих повідомленнях бота (aiogram).

Тонкий шар: приймає /start, натискання кнопок і текст для пошуку, а що
показати — вирішує app/views.py. Працює в тому самому процесі, що й цикл
опитування, і читає той самий стан у пам'яті — тож меню бачить свіжий
зріз одразу після щоденного дайджесту.

Отримувати оновлення Telegram (getUpdates) може лише ОДИН процес на токен —
другий отримає 409 Conflict. Це ще одна причина для single_instance.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import views
from app.artifacts import CATALOG, Artifact
from app.constants import DEEP_LINK_PREFIX
from app.http import default_ssl_context

log = logging.getLogger(__name__)


def to_markup(keyboard: views.Keyboard) -> Optional[InlineKeyboardMarkup]:
    """Кнопки з views -> InlineKeyboardMarkup aiogram. None, якщо кнопок немає."""
    if not keyboard:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in keyboard
        ]
    )


def create_bot(token: str) -> Bot:
    """Bot із HTML-розміткою й тим самим SSL-контекстом, що й httpx.

    `_connector_init` — внутрішнє поле AiohttpSession (aiogram закріплено на
    3.15.0). Без нього aiohttp бере тільки certifi й падає з
    CERTIFICATE_VERIFY_FAILED там, де антивірус підміняє TLS — те саме, що
    app/http.py лагодить для httpx.
    """
    session = AiohttpSession()
    session._connector_init["ssl"] = default_ssl_context()
    return Bot(
        token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def region_from_payload(payload: Optional[str], regions: list[str]) -> Optional[str]:
    """Регіон із deep link `market_EU`. None, якщо payload чужий чи регіону немає."""
    if not payload or not payload.startswith(DEEP_LINK_PREFIX):
        return None
    region = payload.removeprefix(DEEP_LINK_PREFIX).upper()
    return region if region in regions else None


def build_router(
    get_state: Callable[[str], dict],
    regions: list[str],
    catalog: tuple[Artifact, ...] = CATALOG,
) -> Router:
    """Обробники меню. Тільки особисті чати — в групах бот мовчить.

    Args:
        get_state: Стан регіону за кодом (storage.get)
        regions: Регіони, які відстежує бот; перший — типовий
        catalog: Каталог артефактів (параметр — для тестів)
    """
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    # Останній обраний регіон користувача — щоб пошук текстом шукав там само.
    # Тільки в пам'яті: після рестарту — типовий регіон, це не страшно.
    user_region: dict[int, str] = {}

    def region_for(user_id: int, requested: Optional[str] = None) -> str:
        if requested in regions:
            user_region[user_id] = requested
            return requested
        return user_region.get(user_id, regions[0])

    def data_for(region: str) -> Optional[views.MarketData]:
        return views.MarketData.from_state(region, get_state(region))

    async def send(message: Message, view: views.View) -> None:
        await message.answer(view.text, reply_markup=to_markup(view.keyboard))

    @router.message(CommandStart())
    async def on_start(message: Message, command: CommandObject) -> None:
        region = region_for(message.from_user.id, region_from_payload(command.args, regions))
        await send(message, views.menu(data_for(region), region, regions))

    @router.message(Command("market"))
    async def on_market(message: Message) -> None:
        region = region_for(message.from_user.id)
        await send(message, views.menu(data_for(region), region, regions))

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await send(message, views.help_view(region_for(message.from_user.id)))

    @router.message(F.text, ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        region = region_for(message.from_user.id)
        await send(message, views.search(data_for(region), message.text, region, catalog))

    @router.message(F.text.startswith("/"))
    async def on_unknown_command(message: Message) -> None:
        # Мовчання на незнайому команду виглядає як "бот не працює" — краще довідка
        await send(message, views.help_view(region_for(message.from_user.id)))

    @router.callback_query()
    async def on_button(callback: CallbackQuery) -> None:
        data = callback.data or ""
        view = views.render_callback(data, get_state, regions, catalog)
        if view is None or callback.message is None:
            await callback.answer()
            return
        region_for(callback.from_user.id, data.split(":")[1])
        try:
            await callback.message.edit_text(view.text, reply_markup=to_markup(view.keyboard))
        except TelegramBadRequest as exc:
            # Повторне натискання тієї ж кнопки — текст не змінився, це не помилка
            if "message is not modified" not in str(exc):
                raise
        await callback.answer()

    return router


COMMANDS = [
    BotCommand(command="market", description="Меню ринку: купити, продати, прайс"),
    BotCommand(command="help", description="Що вміє бот і як читати сигнали"),
    BotCommand(command="start", description="Почати"),
]
DESCRIPTION = (
    "Аналітика ринку артефактів STALCRAFT: що зараз вигідно купити й продати, "
    "прайс за рідкостями й пошук артефакту. Натисніть «Почати» або /help."
)
SHORT_DESCRIPTION = "Що вигідно купити й продати на аукціоні STALCRAFT"


async def configure_profile(bot: Bot) -> None:
    """Зареєструвати в Telegram список команд і опис бота.

    Без цього кнопка "/" у клієнті нічого не підказує, а порожній чат з
    ботом не пояснює, що він уміє. Виклики ідемпотентні — безпечно робити на
    кожному старті. Збій не критичний: меню працює й без цього.
    """
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_short_description(SHORT_DESCRIPTION)
    except TelegramAPIError:
        log.warning("не вдалося зареєструвати команди/опис бота", exc_info=True)


async def run_menu(token: str, get_state: Callable[[str], dict], regions: list[str]) -> None:
    """Слухати оновлення Telegram, поки процес живий.

    Збій меню не повинен зупиняти сповіщення про викиди, тому помилку тут
    логуємо, а не прокидаємо в головний цикл.
    """
    bot = create_bot(token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(get_state, regions))
    try:
        await configure_profile(bot)
        log.info("меню бота слухає особисті повідомлення (/start, /market, /help)")
        await dispatcher.start_polling(bot, handle_signals=False)
    except Exception:
        log.exception("меню бота впало — сповіщення в канал працюють далі")
