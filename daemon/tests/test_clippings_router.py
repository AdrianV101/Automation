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
