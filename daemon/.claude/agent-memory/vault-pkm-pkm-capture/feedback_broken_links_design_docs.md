---
name: Broken links in design docs are pre-existing noise
description: vault_link_health broken-link results in Automation design/plan docs are example placeholder paths, not real issues
type: feedback
---

Do not attempt to fix broken links in `01-Projects/Automation/development/designs/` or `development/plans/` that point to example paths like `[[00-Inbox/news/2026-04-29/item-a]]`, `[[01-Projects/News/entities/<Name>]]`, or `[[wikilink]]`.

**Why:** These are illustrative wikilinks written during brainstorming/planning to show what vault structure would look like. They were never meant to resolve to real files.

**How to apply:** When running vault_link_health after a session, skip broken links in design and plan documents unless the link targets a real note type (ADR, task, research-note, devlog). The single pre-existing orphan task `review-speaker-labelling-and-recognition-approach.md` is also noise — don't try to link it unless the user mentions it.
