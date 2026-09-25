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
    predicted_next: Optional[str] = None  # ISO-8601 передбачений час наступного викиду


def _seconds_between(earlier: str, later: str) -> float:
    """Обчислити різницю в секундах між двома ISO-8601 часами."""
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def _add_seconds(iso_time: str, seconds: float) -> str:
    """Додати секунди до ISO-8601 часу."""
    from datetime import timedelta
    # Парсимо без інтерпретації timezone, щоб `isoformat()` не додав `+00:00`
    dt = datetime.fromisoformat(iso_time.replace("Z", "")) + timedelta(seconds=seconds)
    return dt.isoformat() + "Z"


def diff(region: str, state: dict, response: dict) -> tuple[list[Event], dict]:
    """Повернути (подій, новий_стан).

    state / new_state — малий словник того, що ми вже відправляли:
        {"announced_current": <currentStart>, "announced_end": <previousEnd>,
         "emission_history": [{"start": ..., "end": ..., "duration": ...}, ...]}

    Args:
        region: Код регіону
        state: Попередній стан відстеження
        response: Відповідь від API /emission

    Returns:
        (events, new_state): Список нових подій та оновлений стан
    """
    events: list[Event] = []
    new_state = dict(state)
    history: list[dict] = new_state.get("emission_history", [])

    current_start = response.get("currentStart")
    previous_start = response.get("previousStart")
    previous_end = response.get("previousEnd")

    if current_start and current_start != state.get("announced_current"):
        last_end = state.get("announced_end")
        # Інтервал відпочинку: від закінчення попереднього викиду до початку нового
        interval = _seconds_between(last_end, current_start) if last_end else None

        # Передбачений час наступного викиду (на основі середньої тривалості з історії)
        predicted_next = None
        if history and len(history) >= 2:
            avg_duration = sum(e.get("duration", 0) for e in history) / len(history)
            predicted_next = _add_seconds(current_start, avg_duration)

        events.append(Event(WARNING, region, current_start, interval, predicted_next))
        new_state["announced_current"] = current_start

    if (
        not current_start
        and previous_end
        and previous_end != state.get("announced_end")
        # не спамимо «завершився» при першому запуску
        and state.get("announced_current") is not None
    ):
        duration = _seconds_between(previous_start, previous_end) if previous_start else None

        # Додаємо в історію
        history.append({"start": previous_start, "end": previous_end, "duration": duration})
        new_state["emission_history"] = history[-10:]  # Зберігаємо останні 10

        # Передбачений час наступного викиду (на основі середнього часу відпочинку)
        predicted_next = None
        if len(history) >= 2:
            rest_times = []
            for i in range(1, len(history)):
                rest = _seconds_between(history[i-1]["end"], history[i]["start"])
                rest_times.append(rest)
            avg_rest = sum(rest_times) / len(rest_times)
            predicted_next = _add_seconds(previous_end, avg_rest)

        events.append(Event(ENDED, region, previous_end, duration, predicted_next))
        new_state["announced_end"] = previous_end

    return events, new_state
