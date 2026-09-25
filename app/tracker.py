"""Чистої доменна логіка: із старого стану + відповіді API одержати список подій.

Тут немає ні мережі, ні файлів, ні читання годин — поточний час приходить
аргументом `now`. Тому легко тестується.

Головне правило "тихого старту": подія йде в Telegram тільки якщо вона свіжа
(не старше `announce_max_age` секунд). Старі викиди все одно записуються в стан
та історію — щоб знати таймінги — але нікого не будять. Це і дає тихий запуск,
і не дає перезапуску під час активного викиду анонсувати його вдруге.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.constants import EVENT_ENDED, EVENT_WARNING, HISTORY_LIMIT

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
    WARNING — відпочинок від закінчення попереднього викиду до початку цього;
    ENDED — скільки тривав сам завершений викид.
    None, якщо порівнювати не з чим або дані непридатні."""
    predicted_next: Optional[str] = None
    """ISO-8601 прогноз, значення залежить від kind:
    WARNING — коли цей викид орієнтовно закінчиться (середня тривалість);
    ENDED — коли орієнтовно почнеться наступний (середній відпочинок).
    None, поки в історії немає з чого рахувати."""


def parse_iso(iso_time: Optional[str]) -> Optional[datetime]:
    """Розібрати ISO-8601 в aware-datetime (UTC). None, якщо розібрати не вдалося.

    Приймає і `...Z`, і `...+03:00`, і наївний час (вважаємо його UTC).
    Не кидає виключень: непридатне значення — це None, а не падіння бота.

    Args:
        iso_time: Строка часу з API або зі стану

    Returns:
        Час у UTC або None
    """
    if not isinstance(iso_time, str):
        return None
    text = iso_time.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _to_iso(moment: datetime) -> str:
    """Записати datetime як ISO-8601 в UTC із суфіксом Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_between(earlier: Optional[str], later: Optional[str]) -> Optional[float]:
    """Різниця в секундах між двома ISO-8601 часами.

    None, якщо хоч одне значення непридатне або різниця відʼємна: відʼємний
    інтервал означає кривий стан (наприклад, годинник з'їхав), і показувати
    «-1 г -30 хв» гірше, ніж не показувати нічого.
    """
    start, end = parse_iso(earlier), parse_iso(later)
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds >= 0 else None


def _shift(iso_time: str, seconds: float) -> Optional[str]:
    """Додати секунди до ISO-8601 часу, повернути ISO-8601 в UTC."""
    base = parse_iso(iso_time)
    if base is None:
        return None
    return _to_iso(base + timedelta(seconds=seconds))


def _is_fresh(iso_time: Optional[str], now: datetime, max_age: float) -> bool:
    """Чи подія достатньо свіжа, щоб про неї сповіщати.

    Час у майбутньому (годинник сервера попереду) вважаємо свіжим — він не
    застарілий. Непридатний час свіжим не вважаємо: не знаємо його вік.
    """
    moment = parse_iso(iso_time)
    if moment is None:
        return False
    return (now - moment).total_seconds() <= max_age


def _avg_duration(history: list[dict]) -> Optional[float]:
    """Середня тривалість викиду за історією. Досить одного запису."""
    values = [
        entry.get("duration")
        for entry in history
        if isinstance(entry.get("duration"), (int, float)) and entry["duration"] >= 0
    ]
    return sum(values) / len(values) if values else None


def _avg_rest(history: list[dict]) -> Optional[float]:
    """Середній відпочинок між викидами. Потрібно щонайменше два записи."""
    gaps = []
    for previous, current in zip(history, history[1:]):
        gap = _seconds_between(previous.get("end"), current.get("start"))
        if gap is not None:
            gaps.append(gap)
    return sum(gaps) / len(gaps) if gaps else None


def diff(
    region: str,
    state: dict,
    response: dict,
    now: datetime,
    announce_max_age: float,
) -> tuple[list[Event], dict]:
    """Повернути (подій, новий_стан).

    state / new_state — малий словник того, що ми вже бачили:
        {"announced_current": <currentStart>, "announced_end": <previousEnd>,
         "emission_history": [{"start": ..., "end": ..., "duration": ...}, ...]}

    Вхідний `state` не змінюється (включно з вкладеним списком історії).

    Args:
        region: Код регіону
        state: Попередній стан відстеження
        response: Відповідь від API /emission
        now: Поточний час (aware або наївний UTC)
        announce_max_age: Скільки секунд подія вважається свіжою

    Returns:
        (events, new_state): Список нових подій та оновлений стан
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    events: list[Event] = []
    new_state = dict(state)
    # Глибока копія історії: інакше append нижче мутував би стан, який нам
    # передали, і main.py не побачив би різниці й не зберіг новий стан.
    history = [dict(entry) for entry in state.get("emission_history", []) if isinstance(entry, dict)]

    current_start = response.get("currentStart")
    previous_start = response.get("previousStart")
    previous_end = response.get("previousEnd")

    if current_start and current_start != state.get("announced_current"):
        new_state["announced_current"] = current_start
        if _is_fresh(current_start, now, announce_max_age):
            average = _avg_duration(history)
            events.append(
                Event(
                    WARNING,
                    region,
                    current_start,
                    _seconds_between(state.get("announced_end"), current_start),
                    _shift(current_start, average) if average is not None else None,
                )
            )

    if not current_start and previous_end and previous_end != state.get("announced_end"):
        new_state["announced_end"] = previous_end
        duration = _seconds_between(previous_start, previous_end)
        history.append({"start": previous_start, "end": previous_end, "duration": duration})
        history = history[-HISTORY_LIMIT:]
        new_state["emission_history"] = history
        if _is_fresh(previous_end, now, announce_max_age):
            average = _avg_rest(history)
            events.append(
                Event(
                    ENDED,
                    region,
                    previous_end,
                    duration,
                    _shift(previous_end, average) if average is not None else None,
                )
            )

    return events, new_state
