---
name: Phase 3 news-personal-digest-feedback capture pattern
description: How to handle devlog capture for large multi-task implementation blocks in the Automation project
type: project
---

When a work block completes 10+ implementation tasks in one session (e.g. Phase 3 Tasks A–P), write a single consolidated devlog entry covering all tasks. Do not create per-task devlog entries.

**Why:** The delegation prompt provides the full task list and key decisions verbatim. One well-structured entry with bullet-per-task is more scannable than 15 micro-entries and avoids devlog bloat.

**How to apply:** Group task bullets by module (state DB, prompt, render, runner, callback, config, wiring, docs, integration). Capture key architectural decisions inline (e.g. "persist before edit", "last-write-wins semantics"). Reference the plan and design notes with wikilinks.
