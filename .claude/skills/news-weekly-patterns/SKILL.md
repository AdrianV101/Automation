---
name: news-weekly-patterns
description: Use when the daemon's news-weekly runner instructs you to produce the weekly pattern note for an ISO week. You are given a deterministic, rank-ordered list of entities that recurred across the week's daily master docs; write a synthesized weekly note at 01-Projects/News/weekly/YYYY-WW.md with one section per recurring thread (what it is, how it developed across the week, why it matters), wikilink back to each daily master and the entity stub, and light-enrich each surfaced hub entity stub. Preserve any human "## Notes" section verbatim. Idempotent — safe to re-run for the same ISO week.
---

# News Weekly Pattern Recognition

You turn a week of daily news into a synthesized "what threads actually ran this week" note. The hard counting is already done for you: the runner prompt contains a **`## Recurring threads (deterministic, rank order)`** block — the authoritative backbone. Your job is narrative synthesis and graph-linking, not re-counting.

## Constraints that matter

- **The deterministic ranking is the backbone.** Write a section for the recurring threads given, in roughly rank order, up to the cap stated in the prompt. Do not silently drop a high-rank thread; do not invent recurrence the data doesn't show.
- **At most ONE extra qualitative thread.** If you see a genuine cross-cutting story the frequency counter missed (e.g. one big event described with different entity names each day), you may add exactly one extra section, clearly marked `(agent-identified)`.
- **You own the weekly note and entity-stub additions; you must NOT touch a `## Notes` section** if one exists in the weekly note. The runner SHA-checks `## Notes`-to-EOF and rejects (status `failed_notes_clobbered`) on any change. That section is the user's.
- **Idempotent.** Re-running for the same ISO week must converge: overwrite the weekly note's generated body, and merge (never duplicate) entity-stub backlinks.

## Procedure

### 1. Read inputs

- The runner prompt's `## Recurring threads (deterministic, rank order)` block — your backbone.
- `vault_read("01-Projects/News/interests-profile.md")` — what the user cares about (weights which threads to foreground and how to frame "why it matters").
- The `## Recent ratings` block in the prompt (⭐/👍/👎) — recent signal.
- For each thread, `vault_read` the daily masters listed in the prompt that mention it (skip any that don't exist) to get the specifics of how it developed day to day.

### 2. Write the weekly note

`vault_write` (or `vault_edit` if it already exists) `01-Projects/News/weekly/<iso_week>.md` with this shape:

```markdown
---
type: news-weekly-patterns
iso_week: <YYYY-WW>
created: <today>
tags: [news, weekly-patterns]
---

# News Weekly Patterns — <YYYY-WW> (<first_date> .. <last_date>)

## <Entity / thread name>

<2–4 sentences: what the thread is, how it developed across the week
(reference specific days), why it matters given the interests profile.>

- **Recurred:** <N> days — [[01-Projects/News/daily/<date>-master|<date>]], …
- **Entity:** [[01-Projects/News/entities/<Name>|<Name>]]

## <next thread>
…

## <Thread name> (agent-identified)
<the optional single qualitative thread, if any>
```

Rules:
- One `##` section per thread, rank order. Backlink every daily master the thread appeared in (use the dates from the deterministic block) and the entity stub.
- Keep each section skimmable — this lands in a Telegram teaser.
- If the note already has a human `## Notes` section, regenerate everything ABOVE it and leave `## Notes` and everything below byte-for-byte unchanged.

### 3. Light-enrich each surfaced hub entity stub

For each thread's entity, open `01-Projects/News/entities/<Name>.md` (it exists as an Obsidian-Sync stub) and ensure it has, without removing existing human content:

- a one-line type + description: `**Type:** company | person | repo | topic — <one line>`
- a backlink to this weekly note and the daily masters, added via `vault_add_links` (it dedupes by basename, so re-runs are safe).

Only the threads you wrote sections for. Do not touch unrelated entity stubs.

### 4. Verify before reporting

- Re-read the weekly note: confirm a human `## Notes` section (if any existed in step 2's pre-read) is unchanged.
- Confirm every thread section has its daily-master + entity backlinks.

### 5. Emit the structured summary

End your response with a single fenced JSON block:

```json
{
  "success": true,
  "threads_written": 5,
  "entities_enriched": 5,
  "error": null
}
```

- `success`: `true` only if the note was written and (if a `## Notes` section pre-existed) left intact.
- `threads_written`: number of `##` thread sections written (including the optional agent-identified one).
- `entities_enriched`: number of entity stubs you light-enriched.
- `error`: present and non-null only when `success` is `false` (e.g. interests profile unreadable, MCP unavailable).

The runner parses the LAST fenced `json` block, so progress JSONs earlier are fine.

## Quality bar

- A thread section must say something the daily one-liners didn't: the arc across the week, the escalation, the connection between days.
- Prefer the interests profile + ratings to decide ORDER of emphasis, never to drop a deterministically-recurring thread.
- "This was three unrelated mentions, not a real thread" is a valid call — say so in one line rather than manufacturing a narrative.
