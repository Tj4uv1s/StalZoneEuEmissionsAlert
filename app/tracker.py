"""Чистої доменна логіка: із старого стану + відповіді API одержати список подій.

Тут немає ні мережі, ні файлів, ні читання годин — тільки порівняння переданих
значень. Тому легко тестується.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.constants import EVENT_WARNING, EVENT_ENDED

WARNING = EVENT_WARNING  # викид оголошений або вже йде
ENDED = EVENT_ENDED      # викид завершився


@dataclass(frozen=True)
class Event:
    """Подія трекера."""

    kind: str
    region: str
    at: str  # ISO-8601 строка з API
    interval: Optional[float] = None
    """Секунди між двома точками, значення залежить від kind:
    WARNING — з початку попереднього відомого викиду (період циклу);
    ENDED — скільки тривав сам завершений викид.
    None, якщо порівнювати не з чим (перший помічений викид)."""


def _seconds_between(earlier: str, later: str) -> float:
    """Обчислити різницю в секундах між двома ISO-8601 часами."""
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def diff(region: str, state: dict, response: dict) -> tuple[list[Event], dict]:
    """Повернути (подій, новий_стан).

    state / new_state — малий словник того, що ми вже відправляли:
        {"announced_current": <currentStart>, "announced_end": <previousEnd>}

    Args:
        region: Код регіону
        state: Попередній стан відстеження
        response: Відповідь від API /emission

    Returns:
        (events, new_state): Список нових подій та оновлений стан
    """
    events: list[Event] = []
    new_state = dict(state)

    current_start = response.get("currentStart")
    previous_start = response.get("previousStart")
    previous_end = response.get("previousEnd")

    if current_start and current_start != state.get("announced_current"):
        last_start = state.get("announced_current")
        interval = _seconds_between(last_start, current_start) if last_start else None
        events.append(Event(WARNING, region, current_start, interval))
        new_state["announced_current"] = current_start

    if (
        not current_start
        and previous_end
        and previous_end != state.get("announced_end")
        # не спамимо «завершився» при першому запуску
        and state.get("announced_current") is not None
    ):
        duration = _seconds_between(previous_start, previous_end) if previous_start else None
        events.append(Event(ENDED, region, previous_end, duration))
        new_state["announced_end"] = previous_end

    return events, new_state
