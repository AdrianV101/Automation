from __future__ import annotations

from automation_daemon.hacker_news_adapter import _filter_items


def _story(item_id, score=200, type_="story", time=1, **extras):
    return {"id": item_id, "type": type_, "score": score, "time": time, **extras}


def test_drops_dead_and_deleted():
    items = [
        _story(1),
        _story(2, dead=True),
        _story(3, deleted=True),
        _story(4),
    ]
    result = _filter_items(items, min_points=100, max_items=10)
    assert [it["id"] for it in result] == [1, 4]


def test_drops_non_story_types():
    items = [
        _story(1, type_="story"),
        _story(2, type_="job"),
        _story(3, type_="poll"),
        _story(4, type_="comment"),
        _story(5, type_="story"),
    ]
    result = _filter_items(items, min_points=100, max_items=10)
    assert [it["id"] for it in result] == [1, 5]


def test_enforces_min_points_threshold():
    items = [_story(1, score=99), _story(2, score=100), _story(3, score=500)]
    result = _filter_items(items, min_points=100, max_items=10)
    assert [it["id"] for it in result] == [3, 2]  # score-desc


def test_caps_at_max_items():
    items = [_story(i, score=1000 - i) for i in range(50)]
    result = _filter_items(items, min_points=0, max_items=5)
    assert len(result) == 5


def test_sort_by_score_desc():
    items = [_story(1, score=100), _story(2, score=500), _story(3, score=300)]
    result = _filter_items(items, min_points=100, max_items=10)
    assert [it["id"] for it in result] == [2, 3, 1]


def test_tie_break_newer_time_first():
    items = [
        _story(1, score=200, time=100),
        _story(2, score=200, time=200),  # newer
        _story(3, score=200, time=150),
    ]
    result = _filter_items(items, min_points=100, max_items=10)
    assert [it["id"] for it in result] == [2, 3, 1]


def test_tie_break_lower_id_first_when_score_and_time_match():
    items = [
        _story(2, score=200, time=100),
        _story(1, score=200, time=100),
        _story(3, score=200, time=100),
    ]
    result = _filter_items(items, min_points=100, max_items=10)
    assert [it["id"] for it in result] == [1, 2, 3]


def test_drops_items_missing_score_field_below_threshold():
    """A score=None item is treated as 0 and dropped under any positive threshold."""
    items = [
        {"id": 1, "type": "story", "time": 1, "score": None},
        _story(2, score=200),
    ]
    result = _filter_items(items, min_points=1, max_items=10)
    assert [it["id"] for it in result] == [2]


def test_empty_input_returns_empty():
    assert _filter_items([], min_points=0, max_items=10) == []
