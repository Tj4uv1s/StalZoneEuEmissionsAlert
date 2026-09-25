"""Тести трекера: всі стани, переходи та кривi дані.

Час усюди передається явно (`NOW`), тому тести детерміновані.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.tracker import ENDED, WARNING, diff, parse_iso

NOW = datetime(2026, 9, 9, 15, 0, 0, tzinfo=timezone.utc)
MAX_AGE = 900.0  # 15 хвилин


def at(**delta) -> str:
    """ISO-8601 час відносно NOW: at(minutes=-5) — пʼять хвилин тому."""
    return (NOW + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(state: dict, response: dict, now: datetime = NOW, max_age: float = MAX_AGE):
    """Викликати diff з типовими NOW/MAX_AGE."""
    return diff("EU", state, response, now, max_age)


# --- початок викиду -------------------------------------------------------

def test_fresh_emission_produces_warning():
    events, state = run({}, {"currentStart": at(minutes=-1)})
    assert [e.kind for e in events] == [WARNING]
    assert state["announced_current"] == at(minutes=-1)
    assert events[0].interval is None  # немає попереднього закінчення


def test_same_emission_is_not_reannounced():
    old = {"announced_current": at(minutes=-1)}
    events, state = run(old, {"currentStart": at(minutes=-1)})
    assert events == []
    assert state == old


def test_warning_carries_rest_interval_since_previous_end():
    old = {"announced_end": at(minutes=-91)}
    events, _ = run(old, {"currentStart": at(minutes=-1)})
    assert events[0].kind == WARNING
    assert events[0].interval == 5400  # 1.5 години відпочинку


# --- кінець викиду --------------------------------------------------------

def test_fresh_end_produces_ended():
    old = {"announced_current": at(minutes=-6)}
    events, state = run(
        old,
        {"currentStart": None, "previousStart": at(minutes=-6), "previousEnd": at(minutes=-1)},
    )
    assert [e.kind for e in events] == [ENDED]
    assert state["announced_end"] == at(minutes=-1)
    assert events[0].interval == 300  # тривав 5 хвилин


def test_same_end_is_not_reannounced():
    old = {"announced_current": at(minutes=-6), "announced_end": at(minutes=-1)}
    events, _ = run(old, {"currentStart": None, "previousEnd": at(minutes=-1)})
    assert events == []


def test_end_without_previous_start_has_no_duration():
    old = {"announced_current": at(minutes=-6)}
    events, _ = run(old, {"currentStart": None, "previousEnd": at(minutes=-1)})
    assert events[0].kind == ENDED
    assert events[0].interval is None


def test_active_emission_does_not_emit_ended():
    # Поки викид ЙДЕ, previousEnd — це минулий цикл, про нього вже говорили
    events, _ = run(
        {"announced_current": at(minutes=-1)},
        {"currentStart": at(minutes=-1), "previousEnd": at(hours=-2)},
    )
    assert events == []


# --- тихий старт ----------------------------------------------------------

def test_cold_start_with_old_end_is_silent_but_remembers():
    """Головний баг: запуск бота не має сповіщати про давній викид."""
    events, state = run(
        {},
        {"currentStart": None, "previousStart": at(hours=-5), "previousEnd": at(hours=-4)},
    )
    assert events == []
    assert state["announced_end"] == at(hours=-4)
    assert len(state["emission_history"]) == 1  # таймінги все одно запамʼятали


def test_cold_start_during_old_emission_is_silent():
    """Рестарт під час активного викиду, що почався давно — без анонсу."""
    events, state = run({}, {"currentStart": at(hours=-3)})
    assert events == []
    assert state["announced_current"] == at(hours=-3)


def test_restart_during_fresh_emission_still_announces_once():
    events, state = run({}, {"currentStart": at(minutes=-2)})
    assert [e.kind for e in events] == [WARNING]
    # другий цикл із тим самим currentStart — тихо
    events2, _ = run(state, {"currentStart": at(minutes=-2)})
    assert events2 == []


def test_stale_event_exactly_at_boundary_is_announced():
    events, _ = run({}, {"currentStart": at(seconds=-int(MAX_AGE))})
    assert [e.kind for e in events] == [WARNING]


def test_event_just_past_boundary_is_silent():
    events, _ = run({}, {"currentStart": at(seconds=-int(MAX_AGE) - 1)})
    assert events == []


def test_future_timestamp_is_treated_as_fresh():
    # Годинник сервера попереду нашого — це не «застаріле»
    events, _ = run({}, {"currentStart": at(minutes=2)})
    assert [e.kind for e in events] == [WARNING]


def test_silent_start_then_real_event_announced_with_interval():
    """Після тихого старту перший справжній викид має коректний відпочинок."""
    _, state = run(
        {},
        {"currentStart": None, "previousStart": at(hours=-5), "previousEnd": at(hours=-4)},
    )
    events, _ = run(state, {"currentStart": at(minutes=-1)})
    assert events[0].kind == WARNING
    assert events[0].interval == 4 * 3600 - 60


# --- історія та прогноз ---------------------------------------------------

def test_history_accumulates_and_is_capped_at_ten():
    state = {"announced_current": "seed"}
    for index in range(12):
        start = (NOW - timedelta(hours=20) + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (NOW - timedelta(hours=20) + timedelta(hours=index, minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        _, state = run(state, {"currentStart": None, "previousStart": start, "previousEnd": end})
    assert len(state["emission_history"]) == 10


def test_warning_predicts_end_from_single_history_entry():
    state = {
        "emission_history": [
            {"start": at(hours=-2), "end": at(hours=-2, minutes=4), "duration": 240}
        ]
    }
    events, _ = run(state, {"currentStart": at(minutes=-1)})
    assert events[0].predicted_next == at(minutes=3)  # -1 хв + 4 хв


def test_ended_predicts_next_start_from_two_history_entries():
    state = {
        "announced_current": at(minutes=-6),
        "emission_history": [
            # відпочинок між цими двома — рівно година
            {"start": at(hours=-3), "end": at(hours=-3, minutes=5), "duration": 300},
            {"start": at(hours=-2, minutes=-55), "end": at(hours=-2, minutes=-50), "duration": 300},
        ],
    }
    events, _ = run(
        state,
        {"currentStart": None, "previousStart": at(minutes=-6), "previousEnd": at(minutes=-1)},
    )
    assert events[0].kind == ENDED
    assert events[0].predicted_next is not None


def test_ended_without_enough_history_has_no_prediction():
    events, _ = run(
        {"announced_current": at(minutes=-6)},
        {"currentStart": None, "previousStart": at(minutes=-6), "previousEnd": at(minutes=-1)},
    )
    assert events[0].predicted_next is None  # один запис — відпочинок рахувати нема з чим


def test_history_entries_with_broken_duration_are_ignored_in_average():
    state = {
        "emission_history": [
            {"start": at(hours=-3), "end": at(hours=-3), "duration": None},
            {"start": at(hours=-2), "end": at(hours=-2, minutes=6), "duration": 360},
        ]
    }
    events, _ = run(state, {"currentStart": at(minutes=-1)})
    assert events[0].predicted_next == at(minutes=5)  # тільки 360с, None пропущено


def test_history_with_only_broken_durations_gives_no_prediction():
    state = {"emission_history": [{"start": None, "end": None, "duration": None}]}
    events, _ = run(state, {"currentStart": at(minutes=-1)})
    assert events[0].predicted_next is None


# --- кривi дані, без падінь ----------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        "2026-09-28T09:19:11+00:00+00:00",  # той самий подвійний офсет, що валив бота
        "не час",
        "",
        12345,
        None,
    ],
)
def test_broken_current_start_never_crashes(bad):
    events, state = run({}, {"currentStart": bad})
    assert events == []  # нічого не анонсуємо: вік невідомий
    if bad:
        assert state["announced_current"] == bad  # але запамʼятали, щоб не крутитися


def test_broken_previous_end_is_recorded_without_announce():
    events, state = run({"announced_current": at(minutes=-6)}, {"previousEnd": "не час"})
    assert events == []
    assert state["announced_end"] == "не час"


def test_negative_interval_is_dropped():
    # announced_end пізніше за новий currentStart — кривий стан
    old = {"announced_end": at(minutes=+30)}
    events, _ = run(old, {"currentStart": at(minutes=-1)})
    assert events[0].kind == WARNING
    assert events[0].interval is None


def test_empty_response_produces_nothing():
    events, state = run({"announced_current": at(minutes=-1)}, {})
    assert events == []
    assert state == {"announced_current": at(minutes=-1)}


def test_history_with_non_dict_entries_is_sanitised():
    events, state = run(
        {"announced_current": at(minutes=-6), "emission_history": ["сміття", None, 42]},
        {"currentStart": None, "previousStart": at(minutes=-6), "previousEnd": at(minutes=-1)},
    )
    assert [e.kind for e in events] == [ENDED]
    assert state["emission_history"] == [
        {"start": at(minutes=-6), "end": at(minutes=-1), "duration": 300}
    ]


def test_naive_now_is_treated_as_utc():
    events, _ = run({}, {"currentStart": at(minutes=-1)}, now=NOW.replace(tzinfo=None))
    assert [e.kind for e in events] == [WARNING]


# --- чистота функції ------------------------------------------------------

def test_input_state_is_not_mutated():
    history = [{"start": at(hours=-3), "end": at(hours=-3, minutes=5), "duration": 300}]
    old = {"announced_current": at(minutes=-6), "emission_history": history}
    _, new_state = run(
        old,
        {"currentStart": None, "previousStart": at(minutes=-6), "previousEnd": at(minutes=-1)},
    )
    # вхідний стан і його вкладений список залишились як були
    assert len(history) == 1
    assert old["emission_history"] is history
    # а новий стан справді інший — main.py побачить різницю й збереже
    assert len(new_state["emission_history"]) == 2
    assert new_state != old


# --- parse_iso ------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected_hour",
    [
        ("2026-09-09T14:00:00Z", 14),
        ("2026-09-09T14:00:00+00:00", 14),
        ("2026-09-09T17:00:00+03:00", 14),  # зводимо до UTC
        ("2026-09-09T14:00:00", 14),        # наївний — вважаємо UTC
    ],
)
def test_parse_iso_accepts_known_forms(text, expected_hour):
    moment = parse_iso(text)
    assert moment is not None
    assert moment.astimezone(timezone.utc).hour == expected_hour


@pytest.mark.parametrize("bad", ["", "abc", "2026-13-45T99:99:99Z", None, 5, [], {}])
def test_parse_iso_returns_none_on_garbage(bad):
    assert parse_iso(bad) is None
