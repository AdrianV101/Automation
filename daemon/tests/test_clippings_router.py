from automation_daemon.clippings_router import RouteOutcome, parse_sentinel


def test_parse_routed():
    text = "Working...\nROUTED | 01-Projects/Next Steps/clippings/Job.md | links:3 | plan:01-Projects/Next Steps/plans/p.md"
    o = parse_sentinel(text)
    assert o.kind == "routed"
    assert o.routed_path == "01-Projects/Next Steps/clippings/Job.md"
    assert o.links_added == 3
    assert o.plan_attached == "01-Projects/Next Steps/plans/p.md"


def test_parse_routed_no_plan():
    o = parse_sentinel("ROUTED | 01-Projects/X/a.md | links:0 | plan:none")
    assert o.kind == "routed"
    assert o.plan_attached is None
    assert o.links_added == 0


def test_parse_needs_clarification():
    text = "NEEDS_CLARIFICATION | Which project is this for? | candidates: Next Steps;Automation;Skip"
    o = parse_sentinel(text)
    assert o.kind == "needs_clarification"
    assert o.question == "Which project is this for?"
    assert o.candidates == ["Next Steps", "Automation", "Skip"]


def test_parse_missing_sentinel_is_failed():
    o = parse_sentinel("the agent rambled but emitted no sentinel line")
    assert o.kind == "failed"
    assert o.error


def test_parse_malformed_routed_is_failed():
    o = parse_sentinel("ROUTED | only-one-field")
    assert o.kind == "failed"


from automation_daemon.tools import TOOLS_CLIPPINGS, as_list


def test_tools_clippings_has_move_and_link_and_skill():
    tools = as_list(TOOLS_CLIPPINGS)
    joined = " ".join(tools)
    assert "vault_move" in joined
    assert "vault_add_links" in joined
    assert "vault_read" in joined
    assert "Skill" in tools  # agent must be able to load the clippings-router skill


from pathlib import Path
from unittest.mock import patch
import pytest

from automation_daemon.clippings_router import route_clipping
from automation_daemon.clippings_state import ClippingsStateDB  # noqa: F401  (ensures package import OK)


class _FakeLoopResult:
    def __init__(self, text, error=None):
        self.text_parts = [text]
        self.error = error
        self.turns_used = 4
        self.tool_errors = []
        self.files_written = []
        self.frontmatter_updated = []
        self.links_added = []


@pytest.fixture
def clipping(tmp_path):
    p = tmp_path / "Job.md"
    p.write_text(
        '---\ntitle: "X"\nsource: "https://x.com/j"\n---\nbody\n', encoding="utf-8",
    )
    return p


async def test_route_clipping_returns_routed(clipping, tmp_path):
    fake = _FakeLoopResult(
        "did work\nROUTED | 01-Projects/Next Steps/clippings/Job.md | links:2 | plan:none"
    )
    with patch("automation_daemon.clippings_router.build_agent_options"), patch(
        "automation_daemon.clippings_router.run_agent_loop_streaming",
        return_value=fake,
    ) as run_mock:
        outcome = await route_clipping(
            clipping, tmp_path, routing_targets=["01-Projects/Next Steps/_index.md"],
        )
    assert run_mock.awaited
    assert outcome.kind == "routed"
    assert outcome.routed_path == "01-Projects/Next Steps/clippings/Job.md"
    assert outcome.links_added == 2


async def test_route_clipping_agent_error_is_failed(clipping, tmp_path):
    fake = _FakeLoopResult("partial", error="Agent hit turn limit (60 turns)")
    with patch("automation_daemon.clippings_router.build_agent_options"), patch(
        "automation_daemon.clippings_router.run_agent_loop_streaming", return_value=fake,
    ):
        outcome = await route_clipping(clipping, tmp_path, routing_targets=[])
    assert outcome.kind == "failed"
    assert "turn limit" in outcome.error


async def test_route_clipping_passes_pinned_destination(clipping, tmp_path):
    fake = _FakeLoopResult("ROUTED | 01-Projects/Automation/clippings/Job.md | links:1 | plan:none")
    captured = {}

    async def _capture(user_prompt, options, on_event=None):
        captured["prompt"] = user_prompt
        return fake

    with patch("automation_daemon.clippings_router.build_agent_options"), patch(
        "automation_daemon.clippings_router.run_agent_loop_streaming", side_effect=_capture,
    ):
        await route_clipping(
            clipping, tmp_path, routing_targets=[],
            pinned_destination="01-Projects/Automation", user_guidance="it's for automation",
        )
    assert "01-Projects/Automation" in captured["prompt"]
    assert "it's for automation" in captured["prompt"]
