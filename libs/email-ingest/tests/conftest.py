from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def real_01() -> bytes:
    return _load("plaud_real_01.eml")


@pytest.fixture
def synth_no_dkim() -> bytes:
    return _load("synth_no_dkim.eml")


@pytest.fixture
def synth_dkim_fail() -> bytes:
    return _load("synth_dkim_fail.eml")


@pytest.fixture
def synth_wrong_from() -> bytes:
    return _load("synth_wrong_from.eml")


@pytest.fixture
def synth_no_transcript() -> bytes:
    return _load("synth_no_transcript.eml")


@pytest.fixture
def synth_single_summary() -> bytes:
    return _load("synth_single_summary.eml")


@pytest.fixture
def synth_four_summaries() -> bytes:
    return _load("synth_four_summaries.eml")


@pytest.fixture
def synth_no_image() -> bytes:
    return _load("synth_no_image.eml")


@pytest.fixture
def synth_empty_transcript() -> bytes:
    return _load("synth_empty_transcript.eml")


@pytest.fixture
def synth_single_speaker_named() -> bytes:
    return _load("synth_single_speaker_named.eml")


@pytest.fixture
def synth_mixed_speakers() -> bytes:
    return _load("synth_mixed_speakers.eml")
