# Upgrading

> 日本語版: [upgrading.ja.md](upgrading.ja.md)

How to move an existing knowledge base to a newer ccmemo. Only versions that
need something from you have a section here; everything else upgrades
silently. The [CHANGELOG](../CHANGELOG.md) links here from each such release.

## How upgrades reach you

- The plugin cache is keyed by `plugin.json` version: `/plugin update ccmemo`
  fetches a release only when that version changed, and `/reload-plugins`
  activates it in the running session.
- The hybrid-search index (`.claude/knowledge/.index/kb.db`) is a derived
  cache. When its schema changes, the next search or `kb_index.py` run
  upgrades it in place from the Markdown — nothing is re-embedded and nothing
  needs to be rebuilt by hand.
- Entry conventions are versioned separately from the plugin. Your knowledge
  base declares the conventions it commits to in the frontmatter of
  `.claude/knowledge/CLAUDE.md`:

  ```yaml
  ---
  schema_version: 2
  ---
  ```

  Checks that a newer schema introduces are still *reported* on an older
  corpus by `kb_graph.py lint` and by the post-write hook, but as **advisory**
  findings that never fail the exit code, until you raise the declaration.
  So updating the plugin does not turn a pre-commit lint red; migrating the
  corpus and raising the number is a step you take when ready.
  `kb_graph.py --schema 2 lint` previews what a higher declaration would
  enforce; `CCMEMO_SCHEMA_VERSION=2` does the same for one shell.

## 1.26.x — `description` and link labels become conventions (schema_version 2)

Search results (`kb_search.py --summary`) and the prompt hook now show each
entry's `description`: its *trigger condition*, when to open it. Entries
without one fall back to their lead paragraph, marked `(lead)`, so nothing
breaks — but the point of the summary (choosing one entry without opening
several) only works once descriptions exist.

`kb_graph.py lint` gained four checks, enforced at `schema_version: 2` and
advisory below it: `missing-description`, `description-length` (80–320
characters), `unlabeled-link` (a `see:`/`ref:`/`amends:`/`extends:` line with
nothing after the link) and `amends-` / `extends-unreciprocated`. New entries
written by `/record-knowledge` already carry a description.

### Migrating an existing corpus

1. See where you stand:
   `python3 scripts/kb_graph.py --root .claude/knowledge/entries --schema 2 lint`
   lists every entry that would fail. `kb_graph.py index-md` prints how many
   still lack a description.
2. Add descriptions in batches. A description is not a summary (the title
   already states the conclusion): it names the situations in which a future
   reader should open the entry — the symptom, the question, the decision
   being made — most typical first, 100–300 characters, phrased as
   "open this when …" in the language the entry is written in. Hub entries
   (`synthesis` / `overview`) end by saying they are also the hub for their
   topic's related entries. Write it from the entry's problem or background
   section; do not copy the title, and do not use double quotes inside the
   value. On a 280-entry corpus this took ten batches of 28 entries, each
   delegated to a Sonnet subagent that read the first 60–80 lines of every
   entry, with a machine check after each batch (every entry has the field,
   the shared parser reads it, length in range, no `"` inside). Put the rules
   and five good examples in one file and hand that file to each batch.
3. Label the links: every `- see:` / `ref:` / `amends:` / `extends:` line
   ends with `— why to follow it`, on the same line (the lint reads one line
   at a time; a label wrapped onto the next line counts as missing).
   `amends:` / `extends:` targets must link back, or be superseded by the
   entry.
4. Re-run the lint with `--schema 2` until it is clean, then declare
   `schema_version: 2` in `.claude/knowledge/CLAUDE.md`. From then on the
   checks are enforced, including by the post-write hook on every save.

A pre-commit hook wired as in [link-graph.md](link-graph.md) keeps passing
throughout: advisory findings do not change the exit code until the
declaration is raised.

Note for corpora edited with the redact hook active: a bulk edit passes every
entry through `postwrite_redact_entries.py`, which is also a chance for it to
catch secrets that were already in the body (it did, on the reference corpus:
a raw 1Password item id and a personal e-mail address). It can also
over-match e-mail-shaped strings such as systemd unit names
(`app-…@autostart.service`) or placeholder SSH URLs; restore those with a
shell edit, not with the Edit tool, or the hook fires again.

## 1.24.0 — one frontmatter parser, list-form `tags`, `status` default

Every reader (index, search, graph CLI, prompt hook) now uses the same
parser. Two things change for existing entries, neither needs a rewrite:

- `tags:` written as a YAML list is now read everywhere; before, a list was
  silently parsed as *no tags* by the scripts. The list is the documented
  form for new entries. The single-line `tags: "#a #b"` form remains fully
  supported — do not bulk-rewrite old entries.
- A missing or blank `status:` means `active` in `kb_search.py` /
  `kb_index.py` too (the prompt hook already did this). Entries without a
  status now appear under `--status active` instead of being dropped.
- Tags starting with a digit (e.g. `#1password`) are now recognised; if you
  had worked around this, the workaround can go.
