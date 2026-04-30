from __future__ import annotations

from datetime import date


def build_runner_prompt(target_date: date) -> str:
    """Thin caller-side prompt that hands off to the news-daily-master skill.

    The procedural detail (steps 1-9, verification checklist) lives in the
    skill at .claude/skills/news-daily-master/SKILL.md, loaded via
    setting_sources=["project"]. This prompt only fixes the date and the
    paths the skill needs to operate on.
    """
    iso = target_date.isoformat()
    return (
        f"Generate the news daily master document for target_date={iso}.\n\n"
        f"- Source folder: 00-Inbox/news/{iso}\n"
        f"- Master document: 01-Projects/News/daily/{iso}-master.md\n"
        f"- Project index (categories live here): 01-Projects/News/_index.md\n"
        f"- Entity namespace: 01-Projects/News/entities/\n\n"
        "Use the news-daily-master skill to perform the work."
    )
