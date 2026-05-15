from pkm import TranscriptData, TranscriptSegment, write_raw_transcript


def _make_transcript():
    return TranscriptData(
        job_id="abc-123",
        recorded_at="2026-02-01T14:30:00Z",
        duration_seconds=1920,
        speakers=["Alice", "Bob"],
        segments=[
            TranscriptSegment(0.0, 4.5, "Alice", "Let's talk about the waitlist."),
            TranscriptSegment(4.5, 9.2, "Bob", "Sounds good."),
        ],
        full_text="Alice: Let's talk about the waitlist.\nBob: Sounds good.",
    )


def test_write_raw_transcript(tmp_path):
    path = write_raw_transcript(_make_transcript(), tmp_path)
    assert path.exists()
    content = path.read_text()
    assert "type: transcript" in content
    assert "source: plaud" in content
    assert "tags: [transcript, plaud, auto-generated]" in content
    assert "**Alice:**" in content
    assert "[00:04]" in content
    assert "abc-123" in content


def test_write_raw_transcript_custom_source(tmp_path):
    path = write_raw_transcript(_make_transcript(), tmp_path, source="calendar")
    content = path.read_text()
    assert "source: calendar" in content
    assert "tags: [transcript, calendar, auto-generated]" in content
    assert "plaud" not in content


def test_write_raw_transcript_custom_tags(tmp_path):
    path = write_raw_transcript(
        _make_transcript(), tmp_path, source="voice-memo", tags=["transcript", "voice-memo", "personal"]
    )
    content = path.read_text()
    assert "source: voice-memo" in content
    assert "tags: [transcript, voice-memo, personal]" in content


def _td() -> TranscriptData:
    return TranscriptData(
        job_id="m1", recorded_at="2026-05-15T10:00:00+00:00",
        duration_seconds=60.0, speakers=["Speaker 1"],
        segments=[TranscriptSegment(0.0, 1.0, "Speaker 1", "hi")],
        full_text="hi",
    )


def test_flag_absent_by_default(tmp_path) -> None:
    p = write_raw_transcript(_td(), tmp_path, source="plaud-email")
    assert "speakers_unresolved" not in p.read_text()


def test_flag_present_when_set(tmp_path) -> None:
    p = write_raw_transcript(_td(), tmp_path, source="plaud-email",
                             speakers_unresolved=True)
    assert "speakers_unresolved: true" in p.read_text()


import yaml  # pyyaml is a daemon dep; available in the daemon test env


def _td_speakers(speakers):
    from pkm import TranscriptData, TranscriptSegment
    return TranscriptData(
        job_id="m1", recorded_at="2026-05-15T10:00:00+00:00",
        duration_seconds=60.0, speakers=speakers,
        segments=[TranscriptSegment(0.0, 1.0, speakers[0], "hi")],
        full_text="hi",
    )


def _frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4:end + 1])


def test_normal_names_stay_bare(tmp_path):
    from pkm import write_raw_transcript
    p = write_raw_transcript(_td_speakers(["Speaker 1", "Alice"]), tmp_path, source="plaud-email")
    assert "speakers: [Speaker 1, Alice]" in p.read_text()  # byte-identical to before


def test_yaml_hostile_names_do_not_break_frontmatter(tmp_path):
    from pkm import write_raw_transcript
    hostile = ["Alice, Bob", "weird]name", "key: val", 'has"quote', "  spaced  "]
    p = write_raw_transcript(_td_speakers(hostile), tmp_path, source="plaud-email")
    fm = _frontmatter(p)  # must parse without error
    assert fm["speakers"] == hostile  # round-trips exactly
