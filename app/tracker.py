"""Чистая доменная логика: из старого состояния + ответа API получить список событий.

Здесь нет ни сети, ни файлов — только сравнение. Поэтому легко тестируется.
"""
from __future__ import annotations

from dataclasses import dataclass

WARNING = "warning"  # выброс объявлен или уже идёт
ENDED = "ended"      # выброс завершился


@dataclass(frozen=True)
class Event:
    kind: str
    region: str
    at: str  # ISO-8601 строка из API


def diff(region: str, state: dict, response: dict) -> tuple[list[Event], dict]:
    """Вернуть (события, новое_состояние).

    state / new_state — маленький словарь того, что мы уже отправляли:
        {"announced_current": <currentStart>, "announced_end": <previousEnd>}
    """
    events: list[Event] = []
    new_state = dict(state)

    current_start = response.get("currentStart")
    previous_end = response.get("previousEnd")

    if current_start and current_start != state.get("announced_current"):
        events.append(Event(WARNING, region, current_start))
        new_state["announced_current"] = current_start

    if (
        not current_start
        and previous_end
        and previous_end != state.get("announced_end")
        # не спамим «завершился» при самом первом запуске
        and state.get("announced_current") is not None
    ):
        events.append(Event(ENDED, region, previous_end))
        new_state["announced_end"] = previous_end

    return events, new_state
