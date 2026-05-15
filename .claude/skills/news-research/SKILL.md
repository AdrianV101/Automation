---
name: news-research
description: Use when the daemon's news-research runner instructs you to deep-research the most interesting items in a day's news master document. Selects the agent-judged top items, fetches the full article + a bounded set of linked sources + GitHub READMEs for repo mentions + prior vault context, and appends a "### 🔬 Deep dive" block under each researched item in the master doc — preserving the "## Notes" section verbatim. Idempotent — safe to re-run for the same date.
---

# News Deep-Research

You enrich a day's news master document with deep research on the few items that most warrant it. The master doc already exists (the news-daily-master agent produced it). Your job: pick the most interesting items by judgment, research them within a bounded budget, and append findings in place.

## Constraints that matter

- **You own additions under existing items; you must NOT touch `## Notes` or anything below it.** The runner SHA-checks the `## Notes` section and rejects (status `failed_notes_clobbered`) if a single byte changed. The user owns that section.
- **Bounded depth (hard rule).** Per item: the article itself, then at most **3** additional linked-source fetches total, plus a GitHub README fetch for any `org/repo` mention. **Never `git clone`** — fetch the README via the web only. Stop when you have enough for a tight briefing, even if under budget.
- **Cap.** Research at most the number of items the runner prompt states (default 3). Fewer is fine if only one or two are genuinely worth it.

## Procedure

### 1. Read inputs

- `vault_read("01-Projects/News/daily/<target_date>-master.md")` — the doc you will enrich.
- `vault_read("01-Projects/News/interests-profile.md")` — what the user cares about.
- The runner prompt contains a `## Recent ratings` block (recent ⭐/👍/👎). Treat ⭐ as the strongest "this is interesting" signal; 👎 topics are rarely worth a dive.

### 2. Select items (judgment)

From the master doc's items, pick the ≤N most worth a deep dive, weighing: alignment with the interests profile, ⭐/👍 rating signal, novelty, and cross-day significance. This is a judgment call — do not mechanically take the top N; if only one item deserves it, research one.

### 3. Research each selected item (bounded)

For each:
- `WebFetch` the item's external URL (full article).
- Follow at most 3 additional links total that materially add context (primary sources, the actual paper/repo/announcement — not navigation or related-articles chrome).
- For any `org/repo` GitHub mention, `WebFetch` `https://github.com/org/repo` (the README renders there). No clone.
- `vault_search` / `vault_read` for prior vault context on the key entities (have we covered this company/repo/topic before?).

### 4. Append findings in place

Use `vault_edit` to insert, **directly under the existing item's bullet line**, a block of exactly this shape:

```markdown
  ### 🔬 Deep dive

  <2–4 sentence synthesis: what's actually new, why it matters, what's the catch.>

  - **Key facts:** <specifics — numbers, versions, names>
  - **Sources:** [<title>](<url>), [<title>](<url>)
  - **Vault context:** [[01-Projects/News/entities/<Entity>|<Entity>]] — <one line on prior coverage, or "no prior coverage">
```

Rules:
- Insert under the item, above the next item/category heading. Never below `## Notes`.
- If a `### 🔬 Deep dive` block already exists under that item (re-run), replace it, don't duplicate.
- Keep it tight. This lands in the user's digest; it must be skimmable.

### 5. Verify before reporting

- Re-read the master doc. Confirm the `## Notes` heading and everything below it is byte-for-byte unchanged from step 1.
- Confirm each researched item has exactly one `### 🔬 Deep dive` block.

### 6. Emit the structured summary

End your response with a single fenced JSON block:

```json
{
  "success": true,
  "items_researched": 2,
  "error": null
}
```

- `success`: `true` if you completed the procedure and the `## Notes` verification passed; `false` on an unrecoverable error (master unreadable, MCP unavailable).
- `items_researched`: how many items you appended a deep-dive block to this run.
- `error`: present and non-null only when `success` is `false`.

The runner parses the LAST fenced `json` block, so progress JSONs earlier are fine.

## Quality bar

- A deep dive must add something the one-line master summary didn't: the mechanism, the number, the caveat, the "why now."
- Prefer primary sources over commentary. If the article is itself commentary on a paper/repo, fetch the paper/repo.
- "No prior coverage" is a fine and useful vault-context answer — don't fabricate links.
