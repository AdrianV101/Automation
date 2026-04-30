---
name: news-daily-master
description: Use when generating or updating the daily news master document at `01-Projects/News/daily/<date>-master.md`. Reads source notes from `00-Inbox/news/<date>/`, edits-in-place preserving the `## Notes` section verbatim, updates source-note `entities` frontmatter, and extends the seed-categories list on `01-Projects/News/_index.md` if needed. Use this whenever the daemon's runner instructs you to produce or refresh a news daily master document, or whenever the user asks you to digest a day's news items into a categorized master with entity wikilinks. Idempotent — safe to re-run for the same date.
---

# News Daily Master Document

You are producing one categorized "master document" per day from individual news items captured by the upstream newsletter ingestion pipeline. Each item is a file at `00-Inbox/news/YYYY-MM-DD/<slug>.md` with frontmatter (`source`, `subject`, `received-at`, etc.) and a body rendered from the original email. Your job is to:

1. Categorize each item under a topic heading (AI, Tech, Finance, Politics, Repositories, Law, Global News, …).
2. Write a one-line summary for each item, with inline `[[wikilinks]]` for every named entity (companies, people, repos, topics).
3. Tag the source note with the entities you extracted, so the vault graph picks them up.
4. Extend the seed-categories list if you needed a new category that didn't exist.
5. Preserve any human annotations in the master doc's `## Notes` section across re-runs — never edit anything below that heading.

## Why these constraints matter

The master doc is the substrate for Phase 4 knowledge-graph work and for the user's daily reading routine. Two things break the substrate:

- **Categorical drift.** If "OpenAI funding round" lands under `Finance` one day and `AI` the next, weekly pattern recognition can't see the thread. So you reuse existing categories whenever possible (the seed list, plus anything previously added) and only introduce new ones when an item genuinely doesn't fit.
- **Lost annotations.** The user adds notes under `## Notes` between runs (reactions, follow-up TODOs, "ask X about this"). If you regenerate that section, those notes vanish. So the agreement is simple: you own everything *above* `## Notes`; the user owns `## Notes` and below. The runner SHA-checks the section after your run and rejects the result if it changed.

## Procedure

Follow these steps in order. The runner has already inserted a `running` row in the state DB and is watching for your structured summary at the end.

### 1. Read the project index

```
vault_read("01-Projects/News/_index.md")
```

The frontmatter has a `categories: [...]` field — that's the seed list. Treat these names as canonical. If you read previous days' master docs (step 4), prefer reusing categories that have already appeared.

### 2. Enumerate source items for the target date

```
vault_list("00-Inbox/news/<target_date>/")
```

If the folder is empty or absent, the runner has already short-circuited and you wouldn't be here — but if you somehow get here with no items, emit the JSON summary with `success: true, item_count: 0` and stop. Don't write an empty master doc.

### 3. Read each source item, extract entities, choose a category

For each item:
- `vault_read` the file
- Note the `source`, `subject`, `received-at` from frontmatter, and the URL (look for the canonical link — newsletter bodies usually have a "Read this on the web" or "Original post" link near the top, or a primary article link in the first heading)
- Identify named entities: companies, people, repositories (e.g., `org/repo` GitHub references), products, topics. Be moderate — extract things that would be worth their own page, not every adjective. "Anthropic" yes; "the company" no. "Dario Amodei" yes; "the CEO" no.
- Choose a category, in this order of preference:
  1. A category from the seed list that fits (Finance, Tech, Repositories, Politics, AI, Law, Global News, …)
  2. A category that appeared on a recent day's master doc (read the previous day's master if helpful — use `vault_list("01-Projects/News/daily/")` then `vault_read` on the most recent file before today)
  3. A new category, only if neither of the above fits. Pick a name that's likely to recur (e.g., "Climate" not "Climate Change Policy In April 2026"). Note it for step 6.

### 4. Read the existing master doc (if any) and collect already-represented items

```
vault_read("01-Projects/News/daily/<target_date>-master.md")
```

If it exists, scan it for the existing source-note wikilinks. The format is `[[00-Inbox/news/<target_date>/<slug>|source]]`. Build a set of slugs already represented. Only add NEW items in step 5; don't duplicate existing ones.

If the master doc doesn't exist (first-time run for this date), you'll create it from scratch in step 5.

### 5. Write or update the master doc

The doc layout is fixed:

```markdown
---
type: news-daily-master
target_date: <YYYY-MM-DD>
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
generated_by: claude-opus-4-7
item_count: <int>
categories: [<used categories>]
tags: [news, daily-master]
---

# News Daily Master — <YYYY-MM-DD>

## <Category 1>

- **[<Title>](<external URL>)** — [[00-Inbox/news/<YYYY-MM-DD>/<slug>|source]] — One-sentence summary mentioning [[01-Projects/News/entities/<Entity>|<Entity>]] and [[01-Projects/News/entities/<Other>|<Other>]].

## <Category 2>

- ...

## Notes

<!-- Anything below this heading is human-authored and must not be modified. -->
```

**For a first-time run:** use `vault_write` to create the whole doc. Set the frontmatter exactly as above. Set `created` and `updated` to today's date. The `## Notes` section should end at the heading itself plus the existing comment line — do not add content below.

**For a re-run with an existing doc:** use `vault_edit` to add the new items only. Insert each new item under the matching `## <Category>` heading. If a new category is needed, insert a new `## <Category>` section between the existing categories and the `## Notes` heading. Update the `updated` and `item_count` frontmatter fields. **Do not touch anything below `## Notes`.** If `vault_edit` is awkward for inserting bullets under a heading, prefer `vault_append` with `heading="## <Category>", position="end_of_section"`.

**Item line conventions:**
- The external URL goes in the markdown link `[Title](URL)`. If you can't find a canonical URL, omit the link (just bold the title).
- The source-note wikilink `[[00-Inbox/news/<date>/<slug>|source]]` is mandatory — it's the runner's idempotency key. Don't shorten the path; it must be the full vault-relative path so the runner's pre/post comparison works reliably.
- Entity wikilinks use the explicit form `[[01-Projects/News/entities/<Name>|<Name>]]`. Display text matches the canonical name. Obsidian Sync auto-creates the stub page on first reference.
- Keep summaries to one sentence. The user reads the master doc as a scannable list; long summaries defeat that purpose.

### 6. Update the seed-categories list (if extended)

If you introduced a category that wasn't in `_index.md`'s frontmatter `categories` list:

```
vault_update_frontmatter("01-Projects/News/_index.md", fields={"categories": [<full list including new>]})
```

Append the new category to the existing list — don't replace it. Subsequent days inherit your extension.

### 7. Tag each source note with extracted entities

For each source note you covered (newly added in this run):

```
vault_update_frontmatter(
  "00-Inbox/news/<target_date>/<slug>.md",
  fields={"entities": [<entity names>]},
)
```

If the source note already has an `entities` field (from a prior partial run), merge — don't overwrite. Plain list of names; no type classification (Phase 4 will handle that).

### 8. Verification (mandatory before reporting)

Run this checklist and only report `success: true` if all three pass. If any fails, include the specific issue in the JSON summary's `skipped_items` field.

- **(a) Coverage.** Every source note in `00-Inbox/news/<target_date>/` is either linked from the master doc OR explicitly listed in `skipped_items` with a reason. List `vault_list("00-Inbox/news/<target_date>/")` and check each filename appears in the master doc as a wikilink.
- **(b) Entity tagging.** Every source note covered (i.e., linked from the master doc) has an `entities` frontmatter field that's been written or merged. Re-read each one if you're unsure.
- **(c) Notes section.** Read the master doc one final time and confirm the `## Notes` heading is exactly as it was when you started — same text, same trailing content. The runner will SHA256 this section and reject the run if anything below the `## Notes` line changed.

If you find issues at this stage, fix them before reporting (you have the tools — re-edit, re-update). The verification is the contract; if you flag items in `skipped_items`, the runner records `failed_verification` and surfaces it to the user.

### 9. Emit the structured summary

End your response with a single fenced JSON block matching this schema:

```json
{
  "success": true,
  "item_count": 7,
  "categories": ["AI", "Tech", "Finance"],
  "new_categories": ["Climate"],
  "skipped_items": []
}
```

**Field semantics:**
- `success`: `true` if you completed the procedure and verification passed; `false` if you encountered an unrecoverable error (e.g., couldn't read a source note, vault MCP unavailable).
- `item_count`: total items now represented in the master doc (existing + new), not just newly added.
- `categories`: list of every `## <Category>` heading present in the final master doc.
- `new_categories`: subset of `categories` you introduced this run (also persisted to `_index.md` in step 6).
- `skipped_items`: list of strings, one per item you couldn't include or had to skip. Format: `"<slug>: <one-sentence reason>"`. Empty list if all items were covered cleanly.
- `error`: optional, only present if `success: false`. Short error description.

The runner extracts the LAST fenced `json` block in your output, so you can emit progress JSONs earlier if useful — only the last one is parsed.

## Quality bar

- **Categorization is a judgment call, not a guess.** If an item could go in two categories, pick the one that's more useful for cross-day pattern matching. "OpenAI funding" is `AI` (the topic the user cares about across days) more than `Finance` (which is closer to the mechanics).
- **One sentence per item.** Two short clauses are fine. Anything longer is a smell.
- **Entity extraction targets reusable things.** "Anthropic" `[[…|Anthropic]]` is reusable; "the announcement" is not. When in doubt, leave it out — Phase 4 work will retroactively add entities, but it can't easily *remove* low-quality ones.
- **The summary should add information.** Don't just paraphrase the title. Mention the specific company, dollar amount, framework version, etc., that someone scanning the doc would want to know.

## Examples of good item lines

```markdown
- **[Anthropic raises $4B Series E](https://example.com/article)** — [[00-Inbox/news/2026-04-29/anthropic-series-e-ab12cd|source]] — [[01-Projects/News/entities/Anthropic|Anthropic]] closed a $4B round led by [[01-Projects/News/entities/Lightspeed|Lightspeed]] at a $40B valuation, with proceeds going toward training compute.

- **[GPT-5 benchmarks leak](https://example.com/leak)** — [[00-Inbox/news/2026-04-29/gpt5-benchmarks-cd34ef|source]] — Internal evals for [[01-Projects/News/entities/OpenAI|OpenAI]]'s [[01-Projects/News/entities/GPT-5|GPT-5]] show ~12% gains over [[01-Projects/News/entities/Claude|Claude Opus 4.7]] on coding tasks but parity on reasoning.

- **[claude-flow v0.8 released](https://github.com/example/claude-flow/releases/v0.8)** — [[00-Inbox/news/2026-04-29/claude-flow-08-ef56gh|source]] — [[01-Projects/News/entities/claude-flow|claude-flow]] adds parallel agent dispatch and a new `Routine` primitive for cron-scheduled remote agents.
```

## Examples of items to mark as skipped

```json
"skipped_items": [
  "newsletter-x-promo-ab12cd: body is entirely a paywall promo with no article content",
  "weekly-roundup-cd34ef: would require summarizing 20+ links — exceeds one-line item budget; flagged for manual review"
]
```
