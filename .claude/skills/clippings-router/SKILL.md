---
name: clippings-router
description: Use when routing ONE web clipping saved by the Obsidian Web Clipper (currently sitting in `Clippings/`) into the correct PKM project — move it to where similar material lives, add inbound wikilinks from the right index/MOC, and optionally attach it to an active plan. Use whenever the daemon's clippings runner hands you a single clipping plus an enumerated list of routing targets and asks you to file it; honour any `pinned_destination` / `user_guidance` as an override. When the right home is genuinely ambiguous, make zero vault changes and ask for clarification instead of guessing. Idempotent/convergent — safe to re-run after a crash mid-route.
---

# Clippings Router

You are filing exactly ONE web clipping into the user's PKM. The Obsidian Web Clipper drops captured pages into the flat `Clippings/` folder with no project context; your job is to give that page a home: move it next to similar material, wire it into the graph so the relevant project can find it, and — when there's a clearly-related active plan — leave an annotated breadcrumb in that plan. If you genuinely can't tell where it belongs, you change nothing and ask.

Your inputs are already in the prompt the daemon gave you (do not go looking for them yourself):
- **The clipping** — its frontmatter (`title`, `source`, `description`) and its body text.
- **Routing targets** — an enumerated list of active `01-Projects/*/_index.md` files and their `plans/` files. This is the menu; route within it.
- **Optionally** a `pinned_destination` (a vault-relative folder the user pre-decided) and/or `user_guidance` (a free-text instruction). Either one is an override.

## Why these constraints matter

- **One clipping, one decision.** The daemon invokes you once per clipping and parses one line back. Multi-routing, batching, or "I also tidied up these other files" breaks its accounting and risks touching things outside your mandate. Stay on the single clipping you were handed.
- **Ambiguity is a valid, expected outcome — not a failure to paper over.** A misfiled clipping is worse than an unfiled one: it pollutes the wrong project's graph and is hard to find and undo. When two projects are both plausible, or nothing really fits, or the intent is unclear, the honest move is to make zero changes and ask. The daemon will relay your question to the user with your candidate list as quick-reply buttons; a good question is cheaper than a bad guess.
- **Mirror existing structure; don't impose your own.** Each project has organically grown folders. A clipping about a job lands wherever job material already lives in that project, not in a fresh folder you invented. Only create a `clippings/` subfolder when nothing existing fits — an unnecessary new folder fragments the project.
- **Discoverability is the point of the move.** Moving the file is half the job; if nothing links to it, it's still lost. The inbound wikilink from the project's index/MOC/plan is what makes the routing real. Links point FROM the discoverable hub TO the clipping.
- **Re-runs must converge, not duplicate.** The daemon may replay you after a crash — the clipping might already be moved, or half-linked. Every mutation is conditional on "does this already exist?". A second run on an already-routed clipping should add nothing and still emit a clean `ROUTED` line.

## Procedure

Follow these in order. The daemon has a `running` row in its state DB and is watching for your single sentinel line at the very end.

### 1. Override fast-path (pinned destination / user guidance)

If the prompt contains a `pinned_destination` or `user_guidance`, the routing decision is already made — do **not** re-deliberate, do **not** run discovery. Treat the pinned folder (or the folder the guidance names) as the chosen destination and jump straight to step 4's mutation block: move the clipping there, add the inbound link from that project's index, attach a plan only if the guidance explicitly names one, then emit the `ROUTED` line. If `pinned_destination` and `user_guidance` conflict, prefer the explicit `user_guidance` text. The user told you where it goes; respect that over your own judgment.

Otherwise continue to step 2.

### 2. Pick the single best target project

Read the clipping's `title`, `source`, `description`, and skim the body. Match it against the enumerated routing targets — the project this content most advances. Calibration examples:

- A job posting / role description / recruiter outreach → the user's job-search project (e.g. `Next Steps`).
- An article on automated knowledge management, agent infrastructure, or PKM tooling → `Automation` or `Obsidian-MCP`, whichever the content is closer to.
- A GitHub repo or write-up showcasing a Pretext use case → `PretextPlugin`.

Pick exactly one. If you find yourself torn between two projects with roughly equal pull, that is the ambiguity signal — note both and head to the ambiguous branch in step 4.

### 3. Choose the destination folder by mirroring existing structure

Look at how the chosen project is already organised before deciding where the clipping lands:

```
vault_list("01-Projects/<Project>/")
```

and, to see what the project's existing notes group around:

```
vault_neighborhood("01-Projects/<Project>/_index.md")
```

Find where similar material already lives (a `jobs/`, `research/`, `references/`, `inbox/` folder, etc.) and choose that as the destination. Only if nothing existing is a reasonable fit, use `01-Projects/<Project>/clippings/` as the fallback folder. The destination path is `01-Projects/<Project>/<chosen-folder>/<clipping-filename>.md` (keep the clipping's existing filename).

### 4. Confidence branch

Decide: are you confident this clipping belongs in this project at this destination?

**Confident → mutate, then emit `ROUTED`.** Perform these in order, each conditional (idempotent — see step 5):

1. **Move the clipping.** `vault_move("Clippings/<filename>.md", "<destination path>")`. If the file is already at the destination (replay), treat this as a satisfied no-op and continue.
2. **Add the inbound link(s).** Use `vault_add_links` to add a wikilink to the clipping FROM the most relevant hub — the project `_index.md`, a MOC, or a plan — so the clipping is reachable from where someone would look for it. Count how many inbound links you add (usually 1; more only if genuinely distinct hubs each warrant it). Skip adding a link that already exists.
3. **Attach to an active plan, only if one clearly fits.** If the routing targets include an ACTIVE plan the clipping directly informs, append an annotated context link into that plan's Related / context section using `vault_add_links` (or `vault_append` with the plan's context heading, `position="end_of_section"`). Append-only — never overwrite or reorder existing plan content. If no plan clearly fits, attach none; that is normal.

Then emit, as the final line of your response, exactly:

```
ROUTED | <new vault-relative path> | links:<n> | plan:<plan path or none>
```

`<n>` is the integer count of inbound links you added this run (0 is valid on a replay where everything already existed). Use `plan:none` (literal) when no plan was attached.

**Ambiguous → change NOTHING, emit `NEEDS_CLARIFICATION`.** If multiple projects are plausible, none fits well, or the intent is unclear, make zero vault mutations (no move, no links, no plan edit) and emit, as the final line, exactly:

```
NEEDS_CLARIFICATION | <one-line question> | candidates: <c1>;<c2>;...
```

The question is one sentence the user can answer at a glance. Provide 2 to 4 candidates, separated by `;`. Candidates are the realistic destination projects/folders you weighed; one candidate may be `Skip` (meaning "leave it in `Clippings/`"). Example:

```
NEEDS_CLARIFICATION | Is this Pretext write-up reference material for the plugin or a job lead? | candidates: PretextPlugin;Next Steps;Skip
```

### 5. Idempotency / convergence

Assume this might be a replay. Before each mutation, check current state:

- **Move:** if `Clippings/<filename>.md` no longer exists but the destination does, the move already happened — skip it, don't error.
- **Links:** read the hub note (or use `vault_links`) and only add wikilinks that aren't already present. Never create a duplicate link.
- **Plan attachment:** check the plan's context section for an existing link to this clipping before appending; never append a second copy.

A re-run on a fully-routed clipping performs no mutations and still emits a well-formed `ROUTED | <path> | links:0 | plan:<...>` line. Convergence, not duplication.

## Output contract

This is the only thing the daemon reads from you. Get it exactly right.

- Your response MUST end with **exactly one** sentinel line, and it MUST be the **last** line matching a sentinel prefix (the daemon scans from the bottom and takes the last `ROUTED |` / `NEEDS_CLARIFICATION |` line — earlier progress text is fine, but don't emit a second, contradicting sentinel).
- It MUST be one of these two forms, verbatim, pipe-delimited with `|`:

  ```
  ROUTED | <new vault-relative path> | links:<n> | plan:<plan path or none>
  ```

  ```
  NEEDS_CLARIFICATION | <one-line question> | candidates: <c1>;<c2>;...
  ```

- `ROUTED` has **exactly 4** `|`-separated fields. Field 3 is the literal token `links:` immediately followed by an integer (e.g. `links:1`, `links:0`). Field 4 is the literal token `plan:` followed by either a vault-relative plan path or the literal word `none`.
- `NEEDS_CLARIFICATION` has **exactly 3** `|`-separated fields. Field 3 is the literal token `candidates: ` followed by **2 to 4** candidates separated by `;`. Fewer than 2 or more than 4 candidates is a parse failure.
- `NEEDS_CLARIFICATION` means you made **zero** vault mutations. Never emit it after moving or linking anything.
- Any deviation — wrong field count, missing `links:` / `plan:` / `candidates:` token, prose where the path should be, a trailing second sentinel — is treated by the daemon as a hard failure, not a partial success. When in doubt, re-read this section before emitting.

## Quality bar

- **Route, don't reorganise.** You touch the one clipping (move it) and add links into it. You do not refactor the destination project, rename its folders, or "improve" unrelated notes.
- **A specific question beats a confident wrong move.** If the honest answer is "could be two places", ask — with candidates that map to real destinations, not vague themes.
- **The link makes it findable.** Always ask: "If the user goes to this project's index next month, will they stumble on this clipping?" If not, your inbound link is in the wrong place.
- **Quiet on replay.** A re-run that finds everything already done is a success that changed nothing — emit the clean `ROUTED` line and stop. Don't re-link "just to be safe".
