from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# Matches [[01-Projects/News/entities/<Name>|<Alias>]] only. The path prefix
# is mandatory, which excludes source links ([[00-Inbox/news/...|source]])
# and any other wikilink. <Name> is everything up to the '|'; it may contain
# spaces but not ']' or '|'. The closing ']]' is required (malformed,
# unclosed links are ignored).
_ENTITY_LINK_RE = re.compile(
    r"\[\[01-Projects/News/entities/(?P<name>[^|\]]+)\|[^\]]*\]\]"
)
_CATEGORY_HEADING_RE = re.compile(r"^##\s+(?P<cat>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class RecurringThread:
    """An entity recurring across >= threshold distinct days of the week.

    `name` is the canonical entities/ stub name (no alias). `day_count` is
    the number of DISTINCT dates it appeared on (same-day repeats counted
    once). `dates` is ascending. `categories` are the master-doc `##`
    section headings under which it appeared, across the week.
    """
    name: str
    day_count: int
    dates: list[date]
    categories: list[str] = field(default_factory=list)


def _entities_by_section(text: str) -> dict[str, set[str]]:
    """Map entity name -> set of category headings it appeared under.

    A category is the most recent `## Heading` line above the entity link.
    Links before any `## ` heading are bucketed under '' (dropped later).
    """
    cats: list[tuple[int, str]] = [
        (m.start(), m.group("cat")) for m in _CATEGORY_HEADING_RE.finditer(text)
    ]

    def category_at(pos: int) -> str:
        current = ""
        for h_start, h_cat in cats:
            if h_start <= pos:
                current = h_cat
            else:
                break
        return current

    out: dict[str, set[str]] = {}
    for m in _ENTITY_LINK_RE.finditer(text):
        name = m.group("name").strip()
        if not name:
            continue
        out.setdefault(name, set()).add(category_at(m.start()))
    return out


def rank_recurring_threads(
    masters_by_date: dict[date, str],
    *,
    recurrence_threshold: int,
) -> list[RecurringThread]:
    """Rank entities by distinct-day recurrence across the week's masters.

    `masters_by_date` maps each ISO-week date to that day's master-doc text
    (only dates whose master exists need be present). An entity is a
    recurring thread when it appears on >= `recurrence_threshold` DISTINCT
    days. Returned sorted by day_count desc, then name asc (stable).
    """
    days_seen: dict[str, set[date]] = {}
    cats_seen: dict[str, set[str]] = {}
    for d, text in masters_by_date.items():
        for name, cats in _entities_by_section(text).items():
            days_seen.setdefault(name, set()).add(d)
            bucket = cats_seen.setdefault(name, set())
            bucket.update(c for c in cats if c)

    threads: list[RecurringThread] = []
    for name, days in days_seen.items():
        if len(days) < recurrence_threshold:
            continue
        threads.append(RecurringThread(
            name=name,
            day_count=len(days),
            dates=sorted(days),
            categories=sorted(cats_seen.get(name, set())),
        ))
    threads.sort(key=lambda t: (-t.day_count, t.name))
    return threads
