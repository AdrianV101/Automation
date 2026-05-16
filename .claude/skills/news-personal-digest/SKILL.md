---
name: news-personal-digest
description: Use when generating the personalised daily news digest at delivery time. Reads today's master doc + the interests profile + the runner-prefilled `## Recent feedback (last 7 days)` block; never re-rates items. Picks 4-12 items grouped by category, writes 2-4 sentence briefings with a "Why you care" line per item, and returns a structured JSON block for the daemon to render to Telegram with rating buttons. Idempotent — safe to re-run for the same date. Use whenever the daemon's runner instructs you to produce or refresh a personalised news digest.
---

# News Personal Digest

You produce one personalised daily digest from a categorised master document, an interests profile, and a recent-ratings preference block. The daemon delivers your output as inline-keyboard Telegram messages with 👍 / 👎 / ⭐ buttons per item; tomorrow's run will see those ratings as preference signal, so what you choose today shapes what gets surfaced tomorrow.

Your inputs (already prepared by the runner):
- **Target date** — the digest is for this date's master doc.
- **Master doc** at `01-Projects/News/daily/<target_date>-master.md` — the candidate pool, already categorised with entity wikilinks.
- **Interests profile** at `01-Projects/News/interests-profile.md` — Adrian's curated context.
- **Recent feedback block** — already inlined in the prompt under `## Recent feedback (last 7 days)`, grouped by ⭐ / 👍 / 👎.

## Why these constraints matter

- **The feedback block is signal, not items.** Don't re-rate, don't include them in today's output, don't second-guess them. Their job is to tell you what kind of items resonated recently so you can lean into similar topics (and away from rated-down ones) when picking from today's candidates.
- **"Never surface" is a hard filter.** The interests profile lists topics Adrian has explicitly opted out of seeing. Drop matching candidates entirely — don't reword to dodge the filter, don't include "for context".
- **Briefings beat headlines.** The daemon delivers your briefing text as the message body, not a link. Adrian reads it, then decides whether to follow the link. So 2-4 sentences with concrete content (numbers, version strings, named people, mechanism) — not a paraphrase of the title.
- **"Why you care" must be specific.** Generic relevance ("This is interesting because AI") wastes the line. Anchor each one in something concrete from the profile — a project, a personal context detail, a recent rating pattern. If you can't write a specific reason, the item probably doesn't belong in today's digest.

## Procedure

### 1. Read the interests profile

```
vault_read("01-Projects/News/interests-profile.md")
```

The profile has four sections you'll use:
- **Active interests** — wikilinks to project `_index.md` files. Anything adjacent is high signal.
- **Personal context** — life-stage / financial / relational facts that feed "Why you care" lines.
- **Always surface** — named entities and topics that should be highlighted whenever they appear.
- **Never surface** — hard filters (drop matching candidates).

If you need richer grounding for the "Why you care" lines, optionally `vault_read` 1-2 of the project _index files linked under "Active interests". Don't read more than that — token budget matters and the index files are dense.

### 2. Read the master doc

```
vault_read("01-Projects/News/daily/<target_date>-master.md")
```

Every bullet under a `## <Category>` heading is a candidate. The bullet's `[[00-Inbox/news/<date>/<slug>|source]]` wikilink is the source-note path you'll use as `source_path` in the output JSON.

### 3. Treat the feedback block as preference signal

The runner has prefilled `## Recent feedback (last 7 days)` in the prompt above. Read it once. Build a mental model of which topics, entities, and categories Adrian has rated:
- **⭐ items** — strongest positive signal (lean toward similar topics; weight at ~3× a 👍).
- **👍 items** — positive signal.
- **👎 items** — negative signal (push similar topics down, but don't blanket-block — a topic with one 👎 isn't dead).

You will NOT re-rate any of these items in today's output. They are tomorrow's history; your job is today's selection.

### 4. Apply hard filters

Drop every candidate whose title or summary matches anything in the profile's "Never surface" list. Be liberal — if a candidate is even close to a never-surface topic (e.g. "fintech that mentions crypto in passing" against a "Crypto" filter), drop it. The user opted out for a reason.

### 5. Score and pick 4-12 items

For each remaining candidate, score relevance to:
- (i) Active interests + Always surface entries (high weight).
- (ii) Recent ⭐ / 👍 patterns (medium weight).
- (iii) Topics matching recent 👎 (negative weight).

Pick the top 4-12. Aim for 6-10 — that's the readable sweet spot. Diversify across categories: don't stuff one category with seven items just because they all score well. Two strong items in five categories beats seven items in one.

### 6. Read source notes for the picked items only

For each chosen item:

```
vault_read("<source_path from master-doc wikilink>")
```

The source note's body is what grounds your briefing. Pull the concrete details — dollar amounts, version numbers, named people, dates, mechanism — that make the briefing more than a paraphrase of the headline.

#### Deep-dive enrichment

Some master-doc items have a `### 🔬 Deep dive` block beneath them (added by the news-research agent earlier in the chain). When an item you include in the digest has one:

- Fold its synthesis into your "Why you care" line — the deep dive is the sharpest available take; use it.
- If the deep dive has notable "Key facts" or "Sources", surface the single most relevant one inline. Don't dump the whole block; the digest stays skimmable.
- Items without a deep-dive block are briefed exactly as before. Absence is normal — research only covers a few items per day.

### 7. Group by category, order categories

Group your picked items by their master-doc category (the `## <Category>` heading the candidate lived under). Order categories by total signal strength: most-resonant first, so Adrian sees the most-relevant block at the top of the digest. Use the master doc's existing emoji conventions where present — if a category doesn't have an obvious emoji yet, the daemon falls back to "•".

### 8. Verification (mandatory before returning)

Run this checklist. If any fails, fix it before emitting the JSON, OR emit `{"success": false, "error": "<reason>"}` and stop.

- (a) Every item has `source_path`, `title`, `url` (or `null`), `briefing` ≥80 chars, `why_you_care`.
- (b) Total items across all categories is in the range [4, 12].
- (c) No item title or briefing matches a "Never surface" pattern from the profile.
- (d) Every `source_path` appears as a wikilink in the master doc you read in step 2.

### 9. Emit the structured summary

End your response with a single fenced JSON block matching this schema:

```json
{
  "success": true,
  "rating_signal_summary": "1-line description of how recent ratings shaped this digest",
  "categories": [
    {
      "name": "AI",
      "emoji": "🤖",
      "items": [
        {
          "source_path": "00-Inbox/news/2026-05-09/anthropic-update.md",
          "title": "Anthropic ships Claude 4.7",
          "url": "https://anthropic.com/news/claude-4-7",
          "briefing": "Larger context window (1M default) and 30% faster cache hits. Released alongside a Sonnet 4.6 deprecation timeline targeting Q3.",
          "why_you_care": "Daily-driver model for Automation; agent-infra is built around its specific behaviours."
        }
      ]
    }
  ]
}
```

**Field semantics:**
- `success`: `true` only if verification passed.
- `rating_signal_summary`: one sentence describing the shape of recent feedback's effect (e.g. "Boosted AI / Anthropic items based on three recent ⭐; deprioritised generic Series B funding given two recent 👎."). Empty-ish runs (no recent ratings) get something like "No prior ratings — neutral run."
- `categories`: ordered list. Categories with zero items must NOT appear.
- `error`: only on `success: false`. One sentence explaining what failed.

The runner extracts the LAST fenced `json` block in your output, so progress JSONs earlier in the message are fine — only the last one is parsed.

## Quality bar

- **Briefings reference content from the source note.** "Anthropic shipped a new Claude" is a paraphrase. "1M default context, 30% faster cache hits, Sonnet 4.6 deprecation in Q3" earns the line.
- **"Why you care" is anchored.** Tie each one to either a named project (link or topic), a personal-context detail (residency, EUR exposure, etc.), or a recent rating pattern. Generic "this matters because AI" is a smell.
- **Diversity beats stuffing.** A digest with one item in five categories beats seven items in one — variety preserves the daily-routine value.
- **Concrete > clever.** No "the AI world saw a major shake-up today" — name the entities, the numbers, the mechanism.

## Worked example: a good item

```json
{
  "source_path": "00-Inbox/news/2026-05-09/ecb-rate-decision-ab12.md",
  "title": "ECB holds at 3.25%, signals June cut",
  "url": "https://ft.com/...",
  "briefing": "ECB held the deposit rate at 3.25% in line with consensus but Lagarde's press conference language shifted from 'data-dependent' to 'increasingly confident', priced as a 70% probability of a 25bp cut in June. Bund yields fell 8bp on the day.",
  "why_you_care": "Eurozone rate trajectory directly affects personal financial planning; a June cut shifts the timeline for refinancing decisions."
}
```

## Worked example: a verification failure response

If verification fails (e.g., only 3 items survived all filters and no realistic sixth could be reached without violating a hard filter):

```json
{
  "success": false,
  "error": "Only 3 items survived hard filters and ranking; minimum digest size is 4. Master doc had 8 candidates, 4 dropped by 'Never surface' filter, 1 too off-topic."
}
```

The runner records this as `failed_verification` and surfaces it to Adrian. Don't pad with weak items to hit the minimum — surface the gap honestly.
