"""Інтеграційні тести меню на справжньому aiogram: Update -> що бот відправив би в Telegram.

Мережу замінено сесією, що записує запити до Bot API, — тож перевіряється вся
зв'язка: фільтри, deep link, редагування повідомлення по кнопці, пошук текстом.
"""
from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetMe,
    SendMessage,
    SetMyCommands,
    SetMyDescription,
    SetMyShortDescription,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.artifacts import Artifact
from app.constants import MARKET_MIN_LOTS
from app.telegram_bot import build_router, configure_profile

SOLID = MARKET_MIN_LOTS + 5
NOW = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)
CATALOG = (
    Artifact(id="prism", name="Призма", category="other_arts"),
    Artifact(id="hog", name="Ежик", category="biochemical"),
)


def _day(date, prices):
    return {"taken_at": f"2026-09-{date}T17:00:00Z", "prices": prices}


STATE = {
    "market_history": [
        *[_day(f"{20 + i}", {"prism:2": {"median": 1_200_000, "count": SOLID}}) for i in range(7)],
        _day("28", {"prism:2": {"median": 900_000, "count": SOLID}}),
    ]
}


class RecordingSession(BaseSession):
    """Сесія без мережі: запам'ятовує виклики Bot API й повертає правдоподібні відповіді."""

    def __init__(self, fail_edit_with: str | None = None) -> None:
        super().__init__()
        self.calls: list = []
        self.fail_edit_with = fail_edit_with

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="Bot", username="TestBot")
        if isinstance(method, EditMessageText) and self.fail_edit_with:
            raise TelegramBadRequest(method=method, message=self.fail_edit_with)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=1, date=NOW, chat=Chat(id=42, type="private"), text=method.text
            )
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover — не потрібне
        raise NotImplementedError

    async def close(self) -> None:
        pass

    def of(self, kind):
        return [c for c in self.calls if isinstance(c, kind)]


USER = User(id=42, is_bot=False, first_name="Сталкер")


def _message(text: str, chat_type: str = "private") -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=10,
            date=NOW,
            chat=Chat(id=42 if chat_type == "private" else -100, type=chat_type),
            from_user=USER,
            text=text,
        ),
    )


def _button(data: str) -> Update:
    return Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="cb1",
            from_user=USER,
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=11, date=NOW, chat=Chat(id=42, type="private"), from_user=USER, text="меню"
            ),
        ),
    )


async def _feed(*updates, regions=("EU",), state=STATE, session=None):
    session = session or RecordingSession()
    bot = Bot("1:TEST", session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(lambda region: state, list(regions), CATALOG))
    for update in updates:
        await dispatcher.feed_update(bot, update)
    return session


@pytest.mark.asyncio
async def test_start_from_channel_button_opens_market_menu():
    session = await _feed(_message("/start market_EU"))
    [sent] = session.of(SendMessage)
    assert sent.text.startswith("<b>📊 Ринок артефактів EU</b>")
    labels = [b.text for row in sent.reply_markup.inline_keyboard for b in row]
    assert "💰 Купити (1)" in labels


@pytest.mark.asyncio
async def test_plain_start_and_market_command_also_open_menu():
    session = await _feed(_message("/start"), _message("/market"))
    assert len(session.of(SendMessage)) == 2


@pytest.mark.asyncio
async def test_button_edits_same_message_and_answers_callback():
    session = await _feed(_button("b:EU:0"))
    [edited] = session.of(EditMessageText)
    assert "Призма" in edited.text and "−25%" in edited.text
    assert len(session.of(AnswerCallbackQuery)) == 1  # інакше в клієнті крутиться «годинник»


@pytest.mark.asyncio
async def test_pressing_same_button_twice_is_not_an_error():
    session = await _feed(
        _button("m:EU"), session=RecordingSession(fail_edit_with="Bad Request: message is not modified")
    )
    assert len(session.of(AnswerCallbackQuery)) == 1


@pytest.mark.asyncio
async def test_unknown_button_is_just_acknowledged():
    session = await _feed(_button("noop"))
    assert session.of(EditMessageText) == []
    assert len(session.of(AnswerCallbackQuery)) == 1


@pytest.mark.asyncio
async def test_text_message_searches_artifact():
    session = await _feed(_message("призма"))
    [sent] = session.of(SendMessage)
    assert sent.text.startswith("<b>🔎 Призма</b>")


@pytest.mark.asyncio
async def test_search_uses_region_last_chosen_by_user():
    other = {"market_history": [_day("28", {"hog:5": {"median": 5, "count": SOLID}})]}

    async def run():
        session = RecordingSession()
        bot = Bot("1:TEST", session=session)
        dispatcher = Dispatcher()
        states = {"EU": STATE, "RU": other}
        dispatcher.include_router(build_router(states.get, ["EU", "RU"], CATALOG))
        await dispatcher.feed_update(bot, _message("/start market_RU"))
        await dispatcher.feed_update(bot, _message("ежик"))
        return session

    session = await run()
    search_reply = session.of(SendMessage)[-1]
    assert "🟡 <b>Легендарный</b> — 5" in search_reply.text


@pytest.mark.asyncio
async def test_bot_stays_silent_in_groups():
    session = await _feed(_message("/start", chat_type="supergroup"), _message("призма", chat_type="group"))
    assert session.of(SendMessage) == []


@pytest.mark.asyncio
async def test_help_command():
    session = await _feed(_message("/help"))
    [sent] = session.of(SendMessage)
    assert sent.text.startswith("<b>📊 Бот ринку артефактів STALCRAFT</b>")


@pytest.mark.asyncio
async def test_unknown_command_gets_help_not_silence():
    """Мовчання на незнайому команду виглядає як "бот не працює"."""
    session = await _feed(_message("/help_me"))
    [sent] = session.of(SendMessage)
    assert "/market" in sent.text
    assert "🔎" not in sent.text  # не сприйнято як пошук артефакту


@pytest.mark.asyncio
async def test_profile_registers_commands_and_description():
    session = RecordingSession()
    await configure_profile(Bot("1:TEST", session=session))

    [commands] = session.of(SetMyCommands)
    assert [c.command for c in commands.commands] == ["market", "help", "start"]
    assert commands.scope.type == "all_private_chats"
    assert session.of(SetMyDescription) and session.of(SetMyShortDescription)


@pytest.mark.asyncio
async def test_profile_failure_is_not_fatal():
    class FailingSession(RecordingSession):
        async def make_request(self, bot, method, timeout=None):
            raise TelegramBadRequest(method=method, message="Bad Request: boom")

    await configure_profile(Bot("1:TEST", session=FailingSession()))  # не кидає
