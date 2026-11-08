from __future__ import annotations

from datetime import datetime, timedelta, timezone

from automation_daemon.plaud_mcp_trigger import (
    TrackState,
    decide,
    is_generic_speaker,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
G = {"Speaker 1": "Speaker 1", "Speaker 2": "Speaker 2"}
P = {"Speaker 1": "Joel Low (Scope)", "Speaker 2": "Speaker 2"}
N = {"Speaker 1": "Joel Low (Scope)", "Speaker 2": "Adrian Verhoosel"}
KW = {"quiet_s": 600, "fallback_s": 86400}


def test_is_generic() -> None:
    for label in ("Speaker 1", "speaker_2", "Speaker3", " Speaker 10 "):
        assert is_generic_speaker(label)
    for label in ("Joel Low (Scope)", "Speakerphone", ""):
        assert not is_generic_speaker(label)


def test_first_sight_all_named_ingests() -> None:
    assert decide(None, N, T0, **KW) == (True, TrackState(T0, N, None))


def test_first_sight_partial_is_baseline() -> None:
    ingest, state = decide(None, P, T0, **KW)
    assert ingest is False
    assert state.last_label_change_at is None


def test_untouched_waits_until_fallback() -> None:
    prev = TrackState(T0, G, None)
    assert decide(prev, G, T0 + timedelta(hours=23, minutes=59), **KW)[0] is False
    assert decide(prev, G, T0 + timedelta(hours=24), **KW)[0] is True


def test_change_starts_quiet_timer() -> None:
    prev = TrackState(T0, G, None)
    now = T0 + timedelta(minutes=5)
    ingest, state = decide(prev, P, now, **KW)
    assert ingest is False
    assert state.last_label_change_at == now
    assert state.speaker_map == P


def test_quiet_period() -> None:
    prev = TrackState(T0, P, T0 + timedelta(minutes=5))
    assert decide(prev, P, T0 + timedelta(minutes=14, seconds=59), **KW)[0] is False
    assert decide(prev, P, T0 + timedelta(minutes=15), **KW)[0] is True


def test_change_resolving_all_ingests_now() -> None:
    prev = TrackState(T0, P, T0 + timedelta(minutes=5))
    assert decide(prev, N, T0 + timedelta(minutes=6), **KW)[0] is True


def test_new_key_counts_as_change() -> None:
    prev = TrackState(T0, G, None)
    now = T0 + timedelta(minutes=1)
    _, state = decide(prev, G | {"Speaker 3": "Speaker 3"}, now, **KW)
    assert state.last_label_change_at == now
