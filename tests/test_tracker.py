from app.tracker import ENDED, WARNING, diff


def test_new_emission_produces_warning():
    events, state = diff("RU", {}, {"currentStart": "2026-09-09T14:00:00Z"})
    assert [e.kind for e in events] == [WARNING]
    assert state["announced_current"] == "2026-09-09T14:00:00Z"
    assert events[0].interval is None  # не с чем сравнить — первый замеченный


def test_same_emission_is_not_reannounced():
    old = {"announced_current": "2026-09-09T14:00:00Z"}
    events, state = diff("RU", old, {"currentStart": "2026-09-09T14:00:00Z"})
    assert events == []
    assert state == old


def test_emission_end_after_known_emission():
    old = {"announced_current": "2026-09-09T14:00:00Z"}
    events, state = diff(
        "RU", old, {"currentStart": None, "previousEnd": "2026-09-09T14:20:00Z"}
    )
    assert [e.kind for e in events] == [ENDED]
    assert state["announced_end"] == "2026-09-09T14:20:00Z"


def test_first_run_with_no_active_emission_is_silent():
    events, state = diff(
        "RU", {}, {"currentStart": None, "previousEnd": "2026-09-09T10:00:00Z"}
    )
    assert events == []


def test_new_emission_carries_interval_since_previous():
    # Інтервал від закінчення попереднього викиду до початку нового
    old = {"announced_end": "2026-09-09T14:00:00Z"}
    events, _ = diff("RU", old, {"currentStart": "2026-09-09T15:30:00Z"})
    assert events[0].kind == WARNING
    assert events[0].interval == 5400  # 1.5 часа відпочинку


def test_ended_emission_carries_its_own_duration():
    old = {"announced_current": "2026-09-09T14:00:00Z"}
    events, _ = diff(
        "RU",
        old,
        {
            "currentStart": None,
            "previousStart": "2026-09-09T14:00:00Z",
            "previousEnd": "2026-09-09T14:04:30Z",
        },
    )
    assert events[0].kind == ENDED
    assert events[0].interval == 270  # 4.5 минуты


def test_emission_history_tracking():
    # Перша подія
    events1, state1 = diff(
        "RU",
        {"announced_current": "2026-09-09T14:00:00Z"},
        {
            "currentStart": None,
            "previousStart": "2026-09-09T14:00:00Z",
            "previousEnd": "2026-09-09T14:05:00Z",
        },
    )
    assert events1[0].kind == ENDED
    assert "emission_history" in state1
    assert len(state1["emission_history"]) == 1

    # Друга подія
    events2, state2 = diff(
        "RU",
        state1,
        {
            "currentStart": "2026-09-09T15:00:00Z",
            "previousStart": "2026-09-09T14:00:00Z",
            "previousEnd": "2026-09-09T14:05:00Z",
        },
    )
    assert events2[0].kind == WARNING
    # На основі однієї попередної подіїї прогнозу немає
    assert events2[0].predicted_next is None


def test_predicted_next_with_history():
    # Будуємо історію
    old_state = {
        "announced_end": "2026-09-09T14:05:00Z",
        "emission_history": [
            {"start": "2026-09-09T10:00:00Z", "end": "2026-09-09T10:05:00Z", "duration": 300},
            {"start": "2026-09-09T11:00:00Z", "end": "2026-09-09T11:05:00Z", "duration": 300},
        ],
    }
    events, _ = diff(
        "RU",
        old_state,
        {"currentStart": "2026-09-09T15:00:00Z"},
    )
    assert len(events) == 1
    assert events[0].kind == WARNING
    assert events[0].predicted_next is not None  # Має прогноз на основі історії
