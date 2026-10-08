# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- `lint --strict-yaml`: an opt-in comparison of each entry's frontmatter with
  PyYAML (#72). It makes two checks and never compares types:
  `yaml-strict-rejected` when `yaml.safe_load` raises (the detail is the
  PyYAML error with its line and column), and `yaml-strict-mismatch` when
  `yaml.BaseLoader` (every value read as text) and ccmemo's parser disagree
  (the detail lists the top-level keys that differ, not the values). Both are
  always advisory: they never change the exit code and are not tied to a
  `schema_version`. Typed values (`yes`, `null`, numbers, dates) and keys that
  are typed words (`{"null": c}`) are out of scope. Without PyYAML the flag
  prints one `yaml-strict: skipped` line on stderr and the `--json` output is
  unchanged. It runs from `lint` only, not `load_graph`, and a fault in one
  entry's check becomes a `could not be checked (...)` finding. The hook still
  needs only the standard library.

## [1.31.0] - 2026-10-06

### Added
- Lint check `yaml-unsafe-value` (schema 4): frontmatter values in forms
  that a strict YAML reader rejects or reads differently (#68). ccmemo's
  parser splits a key at the first `:` and treats `#` as a comment only when
  whitespace follows, so it reads `title: ADR: x` and `title: issue #12 fix`
  as written, and the lint, which uses the same parser, never saw a problem.
  Other readers of the same files reject the first frontmatter outright and
  read the second title as `issue`. The check flags an unquoted value that
  contains `: ` or ends with `:`, contains ` #`, or starts with a character
  YAML reads as syntax, or is only a comment; a `"` or `\` inside double
  quotes not written as `\"` or `\\`, or a `'` inside single quotes not
  doubled; a comment after a closing quote, a comment or other text after a
  flow collection, a flow collection not closed on its line or with a nested
  collection containing commas, and an item inside `[...]`/`{...}` in any of
  these forms or with a quote character inside; a `|`/`>` block, which the
  parser reads as the indicator alone; a one-line value wrapped onto a
  deeper line, which the parser drops along with the keys after it; and a
  line that is neither a key nor a list item, which the parser skips. Each
  finding names its fix. A fault in the check itself is reported as a
  finding, so it cannot stop the other `kb_graph.py` commands. YAML's typed scalars (`yes`, `null`, numbers)
  are out of scope. It is advisory until a knowledge base declares
  `schema_version: 4` (see docs/upgrading.md). On one real knowledge base of
  about 320 entries it listed 6, the same 6 that PyYAML rejected or read
  differently there. Parsing itself is unchanged.

### Changed
- The `/record-knowledge` template quotes `title:`, and the note that titles
  may contain `: ` unquoted is replaced by advice to quote free-text values.
  The README and docs/examples.md examples quote their titles too.
- Both knowledge-base scaffolds (`skills/record-knowledge/assets/` and
  `templates/knowledge/`, which still declared 2) now declare
  `schema_version: 4`; they contain no entries to fix.
- `migrate --to 3` ends by pointing at schema 4, which adds no fields to
  migrate: quote what `--schema 4 lint` lists, then declare 4.

## [1.30.5] - 2026-10-06

### Fixed
- `kb_search.py --kind` could return nothing although entries of that kind
  matched the query (#69). The filter ran after the candidates were taken
  from the whole index, so a larger corpus of another kind on the same theme
  filled the vector arm's 40 slots and pushed every query term over the
  lexical arm's 50-file limit. `--kind` now narrows both arms first: the
  lexical arm counts hits within the requested kinds, and the vector arm
  ranks only chunks of those kinds by `vec_distance_L2` (vec0's own metric).
  The issue proposed over-fetching with a larger `k` instead; sqlite-vec caps
  `k` at 4096, below the chunk count of a real index, so it could not reach
  every chunk. Without `--kind` the ranking is unchanged. On one real index,
  a query that gave 0 `kb` hits now gives 5, and three queries without
  `--kind` return the same list as before.
- An empty `kind` (an index from before schema 5, read-only from a linked
  worktree, is never backfilled) counts as `kb` in the vector arm too, as it
  already did in the stored metadata.
- Run with a plain `python3` that lacks fastembed or sqlite-vec, the CLI died
  with a `ModuleNotFoundError` traceback as soon as an index existed (only
  `--no-lazy` on a root without an index got through). It now runs the
  ripgrep-only fallback the recall skill describes: it never opens the
  index, ranks by the lexical arm alone (`--kind` still applies, from the
  path rules), and says on stderr that `uv run` gives the hybrid search.
  Filters that read stored metadata (`--status`, `--tag`, `--type`,
  `--created-from`, `--created-to`, `--verified`) drop every hit there; the
  CLI says so when one is given, instead of a bare "no hits".

## [1.30.4] - 2026-10-03

### Fixed
- The PostToolUse link checker reported links written as examples inside
  inline code as broken (#65), for instance `[title](path)` in this
  changelog, the skill procedures and the docs. It now blanks code spans
  before extracting links: backtick runs of the same length, which may
  continue onto the next line but no further. CommonMark lets a span run on
  through its paragraph; the cap keeps one stray backtick in a tight list
  from pairing with backticks further down and hiding the real links
  between them. Fenced blocks are
  removed first, so a fence is never read as a code span, and `~~~` fences
  count as well as backtick fences. Over this repository and one knowledge
  base, 18 example links are no longer reported and no new report appears.

## [1.30.3] - 2026-10-03

Leak-scan stops flagging ordinary knowledge entries, and its warnings reach
Claude again by default. Set `CCMEMO_LEAK_SCAN_WARN=0` to keep them off.

### Fixed
- `hooks/lib/leak_scan.py` flagged every entry of a real knowledge base
  (313 of 313). An entry profile drops what is not a leak: the frontmatter
  `id` UUID; a private repo name inside the repository of that name
  (`scan(text, own_repo=...)`, which the redact hook and the auto-commit pass
  as the main checkout's directory name); `${…}` inside inline code or a
  fenced block; and tokens that only looked high-entropy because `/`, `-` or
  `_` join words (entry links, URL paths, `ENV_NAME=`). A token is word-like
  when it has no upper case and no `+`/`=`, or when it switches between
  lower case, upper case and digits less than 0.3 times per character: 833
  of 837 mixed-case path tokens in the corpus fall below that, 2 of 45,000
  random base64 tokens do. After the change 17 of 313 entries are flagged,
  mostly home paths that name a user. `${CLAUDE_PLUGIN_ROOT}` written in
  prose outside backticks is still reported.
- The auto-commit's leak-scan gate uses the same profile, so it no longer
  blocks on entry links and the repository's own name. Absolute home paths
  in task notes are still findings.

### Changed
- `CCMEMO_LEAK_SCAN_WARN` defaults to on again (1.30.2 turned it off until
  the scan was calibrated); `0` turns the warnings off.

## [1.30.2] - 2026-10-03

Warnings from the PostToolUse hooks now reach Claude. Nothing to do.

### Fixed
- `hooks/postwrite_check_md_links.py`, `hooks/postwrite_kb_lint.py` and
  `hooks/postwrite_redact_entries.py` reported their findings as
  `{"decision": "warn", "reason": ...}`. PostToolUse accepts only `"block"`
  as a decision and drops the reason of any other value, so broken links,
  lint findings, redaction notices and leak-scan warnings never reached the
  model; a live session with 1.30.1 received nothing after writing a broken
  link. They now go out as `hookSpecificOutput.additionalContext`, which
  reaches Claude next to the tool result (`hooks/lib/hook_output.py`).
  Redaction itself was not affected: the file was always rewritten.

### Changed
- The redact hook's leak-scan warnings stay out of Claude's context unless
  `CCMEMO_LEAK_SCAN_WARN=1`, as they in effect were before this release. On
  ordinary entries the scan flags the frontmatter `id`, the repository's own
  name and links between entries, so delivering them would add a list of
  false positives to every entry write (#63). The redaction notice is always
  delivered.

### Added
- `tests/test_posttooluse_output.py`: the output shape of the link and
  redaction hooks, leak-scan warnings only with `CCMEMO_LEAK_SCAN_WARN=1`,
  and a check that no hook emits a decision other than `"block"`.

## [1.30.1] - 2026-10-02

The context-guard nudge no longer repeats at every turn of a long session.
Nothing to do.

### Fixed
- `hooks/stop_context_guard.py` checks its condition at every turn. A long
  session's transcript stays above the threshold for good, so once the last
  knowledge-entry write was older than `CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN`
  the model was asked to record at every turn, each costing a turn of its
  own. A nudge now keeps the guard quiet for the same window as an entry
  write: at most one nudge per window. Each nudge leaves an empty marker per
  session in `${XDG_CACHE_HOME:-~/.cache}/ccmemo/context-guard/`, outside
  the repository. The marker's mtime is the nudge time and its content is
  never read, so a torn write cannot silence the guard; a marker dated in
  the future does not count. Its name is a hash of the session id (or of
  the transcript path when there is no id), so any id stays inside the
  cache directory. The marker is written after the nudge is printed, so a
  hook killed earlier does not use up the window. Markers older than seven
  days are pruned one by one. A cache that cannot be written leaves the old
  behavior.
- `tests/test_context_guard.py`: one nudge per window across turns
  (including the `stop_hook_active` second stop), a separate window per
  session, window expiry, a corrupt or future-dated marker, an unwritable
  cache, an id with path characters, no session id, the `~/.cache` default,
  and pruning. Test runs no longer write to the real `~/.cache`.

## [1.30.0] - 2026-10-02

The checkpoint keeps what you asked for, and the active task follows your
branch (#58, #59). Repositories listing more than one active task should add
a `Branch` column to `.claude/tasks/readme.md` (`docs/upgrading.md`, 1.30);
otherwise nothing to do.

### Changed
- Active task lookup (`hooks/lib/tasks.py`, shared by the capture,
  PreCompact and SessionStart(compact) hooks) no longer takes the first
  `## Active` row. In order: the directory named by `CCMEMO_ACTIVE_TASK`;
  the first active row whose optional `Branch` column (fnmatch patterns)
  matches the branch checked out in the hook's `cwd`, which follows a
  worktree; the only active row when exactly one is active and names no
  branch. Otherwise none, so nothing is written into an unrelated task:
  before, a session on another branch appended its captures and its
  `session_state.md` to the first row's task, and the restore showed that
  task. `CCMEMO_ACTIVE_TASK_FALLBACK=first` restores the old choice. Any
  `## ` heading now ends the `## Active` section, not only `## Completed`.
- PreCompact decisions: every prompt the user typed counts, not only those
  containing a word from a fixed keyword list. Short Japanese imperatives
  (〜して, 進めて) never matched, so a long session restored one stale
  consultation and none of the recent requests. The newest ten are kept,
  oldest first, each cut at a sentence or clause boundary and marked `…`.
- `/plan-task` template and procedure: the task index has a `Branch` column,
  filled when a plan is created.

### Added
- PreCompact records `AskUserQuestion` answers as decisions
  (`[header] chosen option`), read from the turn's structured
  `toolUseResult.answers`.
- `tests/test_active_task.py`; decision tests in `tests/test_compact_restore.py`.

### Fixed
- Docs and comments said a PreCompact hook's `systemMessage` is shown to the
  user. The harness discards it; the user does not see it either.

## [1.29.1] - 2026-10-01

Opt-in auto-commit (`CCMEMO_AUTOCOMMIT=1`) fixes. Nothing to do; only users
who enabled auto-commit are affected.

### Fixed
- The safety-net commit swept in files the user had staged outside
  `.claude/knowledge` and `.claude/tasks`: staging was limited to the target
  paths, but `git commit` took the whole index. The commit now carries the
  target paths as its pathspec (an `--only` commit), so the user's staged
  work stays staged and out of the checkpoint.
- A rename staged with `git mv` under a target path made the auto-commit fail
  (`git add -- <old path>`: pathspec did not match). Staging now runs
  `git add -A` over the target directories that exist on disk or are still
  tracked, which records deletions and both sides of a rename.
- `tests/test_autocommit.py`: staged non-target file stays staged; rename and
  delete are committed; a target directory removed entirely is committed.

## [1.29.0] - 2026-10-01

Working state comes back after compaction (#54). The PreCompact hook has
always saved a checkpoint and the active task's `session_state.md`, but
nothing read them back: PreCompact cannot add context and the harness
discards its `systemMessage` (corrected in 1.30.0). No action needed
(`docs/upgrading.md`, 1.29).

### Added
- `hooks/sessionstart_compact_restore.py`, registered as a separate
  `SessionStart` entry with matcher `compact`: right after auto or manual
  compaction it adds the newest checkpoint whose `session_id` matches the
  session, and the active task's `session_state.md` with its `updated:`
  time, as `hookSpecificOutput.additionalContext`, framed as notes rather
  than instructions. Under 8,000 characters (the harness caps
  `additionalContext` at 10,000); a section that does not fit ends with the
  path to read. Read-only, so `/plan-task` still consumes checkpoints.
  Silent without a checkpoint or an active task, for any other source,
  inside harness agent worktrees, and with `CCMEMO_COMPACT_RESTORE=0`.
- `hooks/lib/tasks.py`: `find_active_task_dir()`, previously copied into the
  PreCompact and PostToolUse context hooks, now shared by them and the
  restore hook.
- `tests/test_compact_restore.py`: 34 checks, including end-to-end runs of
  the PreCompact hook followed by the restore hook on transcripts in Claude
  Code's real shape.

### Fixed
- PreCompact never extracted a user decision from a real transcript: it read
  a top-level `content` field, while Claude Code nests each turn under
  `message`. It now reads `message.content` (keeping the top-level shape),
  uses text blocks only (tool results share the `user` type), and skips
  hook feedback (`isMeta`), the compaction summary and harness notices that
  start with a tag (`<task-notification>` …).
- Decisions are taken from the whole transcript, not the 200-line tail, which
  in a long session is all tool traffic; the cap keeps the newest ten (five
  in `session_state.md`) instead of the oldest.
- The restore hook puts a checkpoint's decisions and referenced knowledge
  before its file list, so a long file list is what gets truncated.

### Changed
- PreCompact `systemMessage` no longer tells the model to "read
  session_state.md on resume" (the model never saw it) and the in-code claim
  that it "may be included in compaction summary" is gone; the message now
  reports the counts and that ccmemo restores the state after compaction.

## [1.28.0] - 2026-09-24

Multi-corpus index (#49): the search index can cover the whole repository,
with the knowledge base as one *kind* of corpus among others; one index in
the main checkout, shared read-only by linked worktrees. Designed in the
consumer's ADR (2026-09-24); the default `scope: kb` is unchanged in
behaviour and needs no action (`docs/upgrading.md`, 1.28).

### Added
- `.claude/ccmemo.json` (`hooks/lib/config.py`, stdlib JSON): `index.scope`
  `kb` (default) | `repo`; `extensions` (default `.md .markdown .txt .rst`),
  `include` / `exclude` globs (defaults always excluded: session captures
  `.claude/tasks/**/context-*.md` and `**/.index/**`), `max_file_bytes`
  (256 KB: larger files get metadata only, no embedding — the lexical arm
  still reads them), `corpora: [{kind, path, conventions}]` (longest path
  prefix wins; `kb` is always the knowledge root). ccmemo bakes in no
  repository layout: without a file, `kb` vs `docs` is the only distinction
  and every convention is detected per file. `CCMEMO_INDEX_SCOPE` overrides.
- `scope: repo`: the candidate set is `git ls-files` (tracked plus
  untracked-not-ignored, so a just-written entry stays searchable as before)
  filtered by extension and globs; `.gitignore` keeps the index, secrets and
  build output out; nested repositories and worktrees are not entered. git
  decides the set: when it cannot list a checkout that has a `.git`, the
  run warns and indexes the knowledge base only (removing nothing) rather
  than walk the tree without `.gitignore`; only a directory with no `.git`
  at all is walked, with a warning that ignore rules do not apply. Index
  keys become repository-relative; switching scope re-keys knowledge entries
  without re-embedding. `CLAUDE.md` files are never indexed at any scope.
- `hooks/lib/repo.py`: repository resolution via `git rev-parse
  --git-common-dir`. The index file stays at `<main checkout>/.claude/knowledge/.index/kb.db`
  and is resolved the same from every linked worktree; from a worktree it is
  opened read-only, the lazy refresh is skipped and a one-line staleness
  warning names how many files it is behind on. `kb_index.py` refuses to
  write from a worktree. `CCMEMO_KB_INDEX=<file>` overrides the location and
  lifts the rule (the documented "pin it by hand in a worktree" pitfall is
  gone).
- Index schema 5 (metadata-only upgrade, embeddings untouched):
  `entries.kind`, `size_bytes`, `embedded`; `meta.scope`.
- Edge extractor `markdown-links` (`hooks/lib/edges.py`): inline
  `[text](relative.md)` links, `rel = ref`, label = link text; typed
  `- see:` lines, fenced code, images, `http(s)`/anchor targets and links
  that resolve to no indexed document produce nothing. Runs in `scope: repo`
  on every file; `scope: kb` keeps the pre-1.28 extractor set so its edges —
  and one-hop expansion — do not change.
- `kb_search.py`: `--kind` filter (repeatable, default all), `[kind]` badge
  after the title in ranked and `--summary` output, folding of hits with the
  same sha256 or the same entry `id` into one line with an `also:` line of
  the other locations (`divergent` when a same-`id` copy differs); `--json`
  carries `kind`, `sha256`, `embedded`, `locations`, `divergent`. Title
  falls back to the first `#` heading, then the filename. `status: current`
  satisfies `--status active` for `plain` corpora. The lexical arm runs over
  the index's candidate set, so excluded files never leak in, and orders
  equal scores by path — the same query now gives the same list every run
  (ripgrep's output order varied between runs before).
- `kb_graph.py near-pairs [--kind K,K] [--cross-kind] [--top N]
  [--threshold T]`: closest document pairs by cosine of the whole-document
  vectors read from the index, each marked `linked` / `unlinked`,
  `same-content` / `same-id`; deterministic, structure only; needs the
  `sqlite_vec` module (`uv run --with sqlite-vec`). Lint advisory
  `divergent-mirror`: the same `id` at several paths with different content
  (the copy differing from the knowledge-base one is named; exact mirrors are
  silent). `relink` maps repository-relative move records back to entry
  paths.
- SessionStart hook `hooks/sessionstart_index_prewarm.py`: starts the
  incremental refresh (`uv run --no-project`, so the repository's own
  `pyproject.toml` is never synced unattended) detached and `nice`d when an
  index already exists, in the main checkout only, with a pid lock under
  `.index/` that is ignored once older than six hours; `prewarm.log` is
  truncated past 512 KB; never builds an index that was not built
  explicitly; `CCMEMO_INDEX_PREWARM=0` opts out.
- `tests/test_multicorpus.py`: the issue #49 fixture (temporary git
  repository with a linked worktree) and its invariants; the vector half runs
  under `uv run --with sqlite-vec --with fastembed`.
- Docs: `hybrid-search.md` (multi-corpus configuration reference with
  placeholder names, worktrees), `link-graph.md` (`near-pairs`,
  `divergent-mirror`), `upgrading.md` / `upgrading.ja.md` 1.28 section,
  `architecture.md`; `/recall-knowledge` and `/review-knowledge` procedures
  mention `--kind`, folding and `near-pairs`.

### Changed
- `hooks/post-merge.sample`: the indexer script path variable is
  `CCMEMO_KB_INDEXER` (`CCMEMO_KB_INDEX` now names the index file).

## [1.27.0] - 2026-09-24

### Added
- **Schema version 3** of the entry format, designed as one bundle in the
  consumer's ADR (2026-09-23): a path-independent entry `id` (uuid4) and the
  OKF-style trust family `generated: {by, at}` (required) / `verified:
  [{by, at}]` (append-only) / `stale_after:` (optional). Actors are
  `human:<handle>`, `claude-code[/<model-id>]` or `process:<name>`. The
  trust tier (unverified / machine / human) is derived from `verified`
  only; an event older than `generated.at` is expired. `confidence:` is
  retired — still parsed, used by nothing, dropped from the templates.
  `hooks/lib/trust.py` holds the grammar, the derivation and the line-level
  frontmatter edits the commands below use.
- `kb_graph.py migrate --to 3`: idempotent; inserts `id` and `generated`
  where missing (`at` from the filename's date-time in `--tz`), touches
  nothing else, never backfills `verified`.
- `kb_graph.py verify <entry> --by <actor> [--at]`: appends one verified
  event deterministically and prints the resulting tier.
- `kb_graph.py rename <entry> <new-slug>`: slug only (prefix, author
  segment and directory stay); rewrites every link and `superseded_by:`
  that resolves to the entry, keeping each link's style.
- `kb_graph.py relink`: repairs links to paths the index recorded as
  moved. `kb_index.py` now detects a move (known `id` at a new relpath,
  old file gone), re-keys the rows without re-embedding and records the
  pair in a `moves` table.
- lint: `missing-id`, `duplicate-id`, `missing-generated`, `invalid-actor`
  (enforced from `schema_version: 3`, advisory below); `verification-expired`,
  `stale-after-passed`, `duplicate-title` (informational at every schema,
  printed under their own heading, never fail the lint).
- index (schema 4, metadata-only upgrade): `entries.id`, `generated_by`,
  `generated_at`, `verified_tier`, `verified_at`.
- search: `[human]` / `[machine]` tier marker after the title in `--summary`
  and ranked output (nothing when unverified); opt-in `--verified
  {machine,human}` filter (never affects ranking); `--json` carries `id`,
  `generated`, `verified_tier`, `verified_at`. The `id` is not printed in
  `--summary` (byte budget).
- `docs/upgrading.md` / `upgrading.ja.md`: a 1.27 section — do I need to do
  anything, then `migrate --to 3` → `schema_version: 3` → `lint`.

### Changed
- `/record-knowledge` template and procedure emit `id` and `generated`,
  document the actor grammar and when `generated` is updated; the scaffolded
  KB `CLAUDE.md` declares `schema_version: 3` and replaces the Confidence
  section with Trust. `/review-knowledge` `fix` mode records confirmed
  entries with `verify` (never lint passing). `/recall-knowledge` explains
  the tier marker and `--verified`.
- The prompt-injection hook is unchanged (no latency or format regression).
- `hooks/postwrite_kb_lint.py`: the advisory-only notice names the checks
  and no longer hard-codes schema 2.

Upgrade notes: [docs/upgrading.md](docs/upgrading.md#127-entry-ids-and-verification).

## [1.26.2] - 2026-09-24

### Fixed
- `kb_search.py --summary` named a neighbour by its `YYYYMMDD-HHMMSS`
  filename prefix, which is not unique: entries recorded in the same second
  share it, so the printed id matched several files and
  `kb_graph.py neighborhood <id>` refused it as ambiguous (#45). The index
  now stores a **handle** per entry — the shortest name that is unique in
  the corpus (prefix, else filename, else relpath) — and the summary prints
  that. `--json` carries `handle` on every hit and edge next to `relpath`.
  The index upgrades itself on the next search (metadata only, no
  re-embedding), as in 1.25.

## [1.26.1] - 2026-09-23

### Added
- `schema_version` declaration: a knowledge base states the entry
  conventions it commits to in the frontmatter of
  `.claude/knowledge/CLAUDE.md` (`schema_version: 2`; the scaffolded
  templates carry it). `kb_graph.py lint` enforces the four checks added in
  1.26.0 only at schema 2; on an undeclared corpus (schema 1) they are still
  reported, as `advisory` findings that do not affect the exit code, so
  updating the plugin never turns a pre-commit lint red before the corpus is
  migrated. `--schema N` previews a higher declaration;
  `CCMEMO_SCHEMA_VERSION` does the same per shell; `--json` findings carry
  `severity`.
- `hooks/postwrite_kb_lint.py`: advisory findings are summarised in one line
  instead of listed, so an unmigrated corpus is nudged, not nagged.
- `docs/upgrading.md` (and `docs/upgrading.ja.md`): how upgrades reach a
  corpus and the migration steps for 1.24.0 and 1.26.x (bulk description
  recipe, link labels, raising the declaration). Linked from the README.

Upgrade notes: [docs/upgrading.md](docs/upgrading.md#126-descriptions-and-link-labels).

## [1.26.0] - 2026-09-23

### Added
- `kb_graph.py lint`: four checks that make the trigger-condition
  `description` and the link labels a convention the tooling enforces, not
  a habit — `missing-description`, `description-length` (80–320 chars),
  `unlabeled-link` (a `see:`/`ref:`/`amends:`/`extends:` line with nothing
  after the link) and `amends-unreciprocated` / `extends-unreciprocated`
  (the target of a correction or elaboration neither links back nor is
  superseded by the source). `load_graph` nodes now carry `description`
  and `superseded_by`.
- `hooks/postwrite_kb_lint.py` (PostToolUse on Write/Edit): runs
  `kb_graph.py lint <file>` on every knowledge entry just written and
  returns the findings as a warning in the same turn, so a missing
  description or an unlabeled link is fixed while the entry is still in
  context. Advisory, never blocks; silent on clean entries and non-entries.
- UserPromptSubmit hook: each injected candidate now shows a `when:` line
  with its `description` (cut to `CCMEMO_SEARCH_DESC_CHARS`, default 80),
  so one candidate can be chosen without opening several. Read in the same
  single parser call as status/title — no extra interpreter start.
- record-knowledge procedure and the knowledge `CLAUDE.md` templates:
  `description` is a required field, defined as the entry's trigger
  condition (when to open it), with the writing rules and an example.
- `tests/test_postwrite_kb_lint.py`; new cases in `tests/test_kb_graph.py`
  and `tests/test_knowledge_search_hook.py`.

### Changed
- `lint` on a corpus written before this release reports
  `missing-description` for every entry until descriptions are added
  (the checks are on by default, per the design decision that owns them);
  `kb_graph.py index-md` shows how many are still missing.

## [1.25.0] - 2026-09-23

### Added
- `kb_search.py --summary`: neighbourhood summary mode. Per hit — title,
  frontmatter `description` (falling back to the lead paragraph, marked
  `(lead)`) and the typed links with the label each link carries, in the OKF
  `index.md` shape (`* [Title](path) - description`, edges nested as
  `- see <id> — label`). Meant to pick the one entry worth opening without
  reading any body: ten summaries measure ~6.9 KB on a 288-entry Japanese KB,
  under one entry body. `--edges N` / `--linked-from N` cap the outgoing /
  incoming edges shown (defaults 3 / 0, `-1` = all). `--json` carries the
  uncapped fields (`description_source`, `edges`, `linked_from`, totals).
- Index schema v2: `entries.description` column and an `edges` table
  (`src`, `target`, `rel`, `label`, `ord`) holding every `see` / `ref` /
  `amends` / `extends` link with the one-line label written after it. An
  older index is upgraded in place on the next search or reindex — metadata
  and edges are re-read from the Markdown, nothing is re-embedded.
- `hooks/lib/edges.py`: typed-edge extraction shared by the index and the
  graph CLI (`LINK_RE` / `LOOSE_LINK_RE` now live here). Two extractors run
  on every file: the `- see:`-style body bullets (knowledge-base convention)
  and a frontmatter `related_docs:` list (design-document convention), so a
  corpus can mix conventions without configuration.
- `kb_graph.py index-md [--out FILE]`: the whole KB as an OKF-style
  `index.md` (title + description per entry), a by-product other tools can
  read; reports how many entries still lack a description.
- `scripts/kb_recall_eval.py`: replays a log of search misses
  (query → expected entry pairs, Markdown table or JSON Lines) and reports
  hit@N; `--compare` diffs against an earlier `--json` run so retrieval
  changes are measured rather than assumed.
- `tests/test_edges.py`, `tests/test_kb_search_summary.py` (the sqlite part
  runs when `sqlite_vec` is importable, e.g. `uv run --with sqlite-vec
  --no-project python3 tests/test_kb_search_summary.py`).

### Changed
- One-hop expansion now follows every typed link (`ref` / `amends` /
  `extends` too), previously only `- see:` lines.
- Default search output prints a `when:` line from the frontmatter
  `description` when present; the keyword snippet no longer starts with the
  H1 (it repeated the title).

Upgrade notes: [docs/upgrading.md](docs/upgrading.md#124-tags-and-status).

## [1.24.0] - 2026-09-23

### Added
- `hooks/lib/frontmatter.py`: one YAML-frontmatter parser shared by every
  reader — `kb_index.py`, `kb_search.py`, `kb_graph.py`,
  `regenerate-tag-registry.py` and the UserPromptSubmit hook. Pure stdlib
  (runs under plain `python3` like the hooks and the graph CLI, where PyYAML
  is not available). Reads the subset the entry schema uses — quoted/bare
  scalars, block and flow lists, one level of nested mappings and lists of
  mappings — and returns a normalized view: `tags` always a list of `#tag`
  strings, `status` defaulting to `active`, scalar fields always present.
  A CLI (`--fields`, `--sep`) lets the bash hook read status/superseded_by/
  title for all candidates in one interpreter start.
- `tests/test_frontmatter.py`: parser self-tests, including a cross-check
  against PyYAML on strictly valid fixtures when it happens to be importable.

### Changed
- `tags:` written as a YAML list (`- "#tag"` per line) is now read
  everywhere. Every previous reader was a flat `key: value` splitter, so a
  list-form `tags:` was silently parsed as *no tags* (confirmed on a 388-entry
  corpus written in list form: 0 entries had tags in the graph, now 388).
  The documented canonical form becomes the list; the single-line string
  form stays fully supported — do not bulk-rewrite existing entries.
- Missing or blank `status:` now means `active` in `kb_index.py` /
  `kb_search.py` too. Previously the search scripts stored `""` (so
  `--status active` silently excluded frontmatter-less entries) while the
  prompt hook already defaulted to `active`; the two disagreed on which
  entries exist.
- Tag pattern unified to `#[A-Za-z0-9][A-Za-z0-9_-]*`: `kb_index.py` used to
  require a leading letter and dropped tags such as `#1password`.
- Unquoted titles containing `: ` (e.g. `title: ADR: …`) are kept whole.
  They are not strict YAML — PyYAML rejects them — which is one reason the
  parser is a lenient subset implementation rather than a PyYAML wrapper.

### Fixed
- UserPromptSubmit hook: frontmatter is read by the shared parser instead of
  an ad-hoc awk block, so tag/status semantics can no longer drift from the
  index. Column transport uses the US separator, not tab, because bash
  `read` collapses runs of whitespace IFS characters and an empty
  `superseded_by` shifted the title column.

Groundwork for the multi-corpus index: a `description` field and typed,
labelled `see:` edges are planned next and need one parser that can read them.

## [1.23.1] - 2026-09-22

### Fixed
- `/record-knowledge` procedure: the `link-add` fallback (step 7) only covered
  a non-zero exit and said "edit manually as before" without restating the
  format. An agent with no shell tool could not run the CLI at all, improvised
  hand-written links, and the form drifted to `../../MM/<file>.md` within one
  run — caught by `lint` as `broken-link`, but only after the fact (#39). The
  fallback now also covers "the CLI cannot be run", and restates the line
  inline: `- see: [<target's exact frontmatter title>](YYYY/MM/<filename>.md)
  — <relationship>`, entries/-relative, never `../`, same shape for backlinks.
- "ref / see Link Format" now separates the two cases: `../` is only for an
  in-repo `ref:` whose target lies outside `entries/`; links between entries
  never use it.
- New step 8 (the summary moves to step 9): run `kb_graph.py lint` and confirm
  no finding names the new or edited entries; an agent that cannot run it must
  say "lint not run" in its summary so the caller does.
- The same wording is mirrored where other procedures write list links by
  hand: the Change Flow (`supersede`) fallback in the record-knowledge
  procedure, and `/review-knowledge` Fix Mode (link fixes now point at
  `link-add`, restate the hand-written format, and gain a lint verification
  step). `docs/link-graph.md` describes the fallback contract.

Documentation-only; no code change. The version is bumped because skill
procedures ship through the version-keyed plugin cache.

## [1.23.0] - 2026-09-21

Two mitigations for the known multi-checkout divergence in git-tracked mode
(#24): capture files and hub-entry backlinks edited concurrently in several
checkouts of one repository.

### Added
- `CCMEMO_CAPTURE_CHECKOUT_SUFFIX=1` (opt-in, off by default): the
  PostToolUse context writer names new capture files
  `context-<YYYYMMDD-HHMMSS>-session-<id8>.md` and reuses only today's
  unconsumed file ending in its own `<id8>`, so checkouts never append to each
  other's captures. `<id8>` is the first 8 hex digits of
  `sha256(hostname + NUL + realpath(git toplevel))` (realpath of the working
  directory outside git) from the new `hooks/lib/checkout_id.py`; the hostname
  and the path themselves never reach a filename, file body or log line.
  Without the variable, naming and reuse are unchanged.
- `kb_graph.py union-recover <file> [--theirs <ref>] [--dry-run]`: automates
  the lossless union recovery documented in #24, for a file conflicted by a
  merge/rebase (index stages 1/2/3) or for uncommitted local changes vs a ref
  (common ancestor = merge-base). It writes only when both sides are
  append-only against the ancestor (zero deleted or rewritten lines — so
  frontmatter rewrites and body edits of knowledge entries are refused with
  the reason and a non-zero exit), verifies that every line of every source
  survived, backs the previous file up under the git dir, and never touches
  the index. Re-running on an already-unioned file is a no-op. Duplicate
  `see:` links a union can leave behind are reported by the existing `lint`
  check `duplicate-link`.
- Tests: `tests/test_checkout_id.py` (digest, git-toplevel/cwd fallback,
  unchanged default behavior, own-file-only reuse, no hostname/path leakage)
  and `tests/test_union_recover.py` (both situations end to end through
  `rebase --continue`, dry-run, backup, and the edit-vs-edit refusals).
- Docs: `docs/architecture.md` (Context Guard → Multiple checkouts in
  git-tracked mode), `docs/link-graph.md` (`union-recover`), and a
  customization bullet in `docs/usage.md` / `docs/usage.ja.md`.

## [1.22.0] - 2026-08-17

### Fixed
- The context-guard Stop hook's "recorded recently" suppression no longer
  scans the transcript for the entries-path substring — mere path *mentions*
  (the per-prompt auto-search injection alone contains entry paths, as do
  search results and issue bodies) kept the nudge suppressed almost
  permanently, which was why the hook rarely fired in real sessions.
  Suppression is now judged by entry-file **mtimes** under
  `.claude/knowledge/entries/`, which sees every real write path — direct
  edits, the canonical subagent recording flow (whose Write happens in a
  separate transcript and was invisible to any transcript scan), and
  `kb_graph.py` CLI writes — while being immune to mentions.

### Added
- `CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN` (default 45): how many minutes one
  entry write keeps the nudge quiet.
- Threshold guidance for bounded context windows in
  `docs/architecture.md` (Context Guard → Configuration), including the
  observed transcript-bytes-per-token rule of thumb.
- Tests: `tests/test_context_guard.py` — threshold gate, `stop_hook_active`
  loop guard, mtime suppression and window override, and a regression test
  pinning that path mentions alone no longer suppress.

## [1.21.1] - 2026-08-16

### Fixed
- The keep-names raw-ID gate is now case-insensitive, mirroring `OP_REF`: an
  upper-cased raw 26-char item/vault ID inside an `op://` reference no longer
  slips through `CCMEMO_REDACT_OP_REF=keep-names` (case parser-differential
  flagged by security review of the 1.21.0 change).

### Changed
- The accepted differential vs the shared TS SPEC pattern is documented at
  the `OP_REF` definition: a NON-canonical reference containing a character
  outside the URI charset is masked only up to that character, and the
  surviving tail can only be an item-name fragment — raw IDs are single
  in-charset segments and are always masked whole; canonical references
  percent-encode such characters and are unaffected.
- Docs now name 1Password explicitly for the `op://` bullets: the pattern is
  scoped to 1Password's secret-reference URI scheme and is inert for
  knowledge bases that do not use it.

## [1.21.0] - 2026-08-16

### Fixed
- The `op://` redaction pattern no longer swallows adjacent syntax. The
  greedy `\S+` consumed the closing quote, backtick or paren right after a
  reference (`{{ onepasswordRead "op://…" | trim }}` lost its closing quote;
  `` `op://` `` lost its closing backtick), mangling entry bodies on every
  re-edit. The pattern now matches only the URI charset and must end on an
  alphanumeric, so it stops at quotes, brackets, CJK text and trailing
  punctuation. Applies to every redaction surface (entry profile, structured
  args, free text); a deliberate, documented deviation from the shared TS
  SPEC pattern.

### Added
- `CCMEMO_REDACT_OP_REF=keep-names`: the entry redact hook keeps `op://`
  item-name references (`op://vault/item-name/field` — no secret value; a
  host secrets policy may explicitly allow them) and masks only references
  containing a raw 26-character item/vault ID segment. Default unchanged:
  every `op://` reference is masked.
- Tests: adjacent-syntax preservation (quote/backtick/paren), keep-names
  name-vs-raw-ID split, unchanged default behaviour.

### Changed
- `docs/link-graph.md`: the pre-commit lint section now documents wiring for
  a **consuming** repository. Pointing a hook at the version-keyed plugin
  cache path freezes the lint on an old copy after every update and dies
  silently once the cache is cleared; the docs now recommend a repo-committed
  copy with loud skip paths, with a fail-loud newest-cache-copy resolver as
  the alternative. The in-repo snippet in `scripts/kb_graph.py` carries a
  matching warning.

## [1.20.0] - 2026-08-16

### Added
- `supersede <old> <new>` subcommand in `scripts/kb_graph.py`: the Change Flow
  in one validated step — sets the `status: superseded` + `superseded_by:`
  frontmatter pair, inserts a body-top warning banner
  (`> **⚠ superseded (date)** — current: [title](id)`), and appends an
  `- amends:` back-link to the replacement via the `link-add` machinery
  (skipped when the replacement already links the old entry). Everything is
  validated before anything is written (no partial application), re-running
  completes an interrupted run, and self-supersede, conflicting successors,
  supersede cycles, bracketed replacement titles and anchor-less replacements
  are refused loudly. `--date` and `--dry-run` supported.
- Tests: eight new checks in `tests/test_kb_graph.py` covering the frontmatter
  pair, banner placement, back-link, lint-cleanliness of the result,
  idempotent re-run and self-healing, cycle/conflict refusal, atomicity
  without an anchor, dry-run, and bracket-title refusal.

### Changed
- `record-knowledge` procedure: the Correction Flow is generalized to a
  **Change Flow** — supersede now explicitly covers evolved understanding, not
  just corrections — and the Amendment Rules gained a decision table for
  in-place edit vs `amends:`/`extends:` entry vs supersede, keyed on one
  question: may the old entry still be cited as current knowledge afterwards?
- Warning-banner convention documented for non-active entries (body-top
  blockquote; deliberately not a list-form link so the lineage is not
  double-booked as a graph edge), in the procedure and both distributed
  `CLAUDE.md` templates. The banner is a human aid — machine paths already
  respect `status` since 1.19.0.
- `docs/link-graph.md`: `supersede` documented alongside `link-add`.

## [1.19.0] - 2026-08-16

### Fixed
- The per-prompt auto-search hook (`hooks/userpromptsubmit_knowledge_search.sh`)
  now respects frontmatter `status`: `superseded` / `deprecated` entries are
  no longer injected as current knowledge. Entries
  without a `status:` line keep surfacing (treated as `active`), the field is
  read from the frontmatter block only (a body line starting with `status:`
  cannot leak in), and filtered-out entries do not consume result slots — the
  hook walks the ranking until `MAX_RESULTS` allowed entries are collected.
  `kb_search.py --status` and the entry templates already treated non-active
  entries as reference-only; the always-on injection path was the one place
  that ignored it.
- A title-less entry file no longer aborts the whole hook (`rg` exiting 1
  under `pipefail` made the `basename` fallback unreachable).

### Added
- `CCMEMO_SEARCH_STATUS`: comma-separated allowlist of statuses the auto-search
  hook may surface (default `active`; `all` disables filtering). Non-active
  entries deliberately surfaced this way are annotated with
  `[status: …, superseded_by: …]` so the reader sees they are not current.
- Tests: `tests/test_knowledge_search_hook.py` — runs the hook end-to-end with
  a stubbed `mecab`; covers default exclusion, missing-status fallback,
  allowlist widening with annotation, `all`, slot preservation after
  filtering, and frontmatter-only field extraction.

## [1.18.0] - 2026-08-16

### Added
- `malformed-link` lint check (#27): a line that looks like a list link
  (`- see: [` … — loose pattern over `see`/`ref`/`amends`/`extends`) but does
  not parse as one — e.g. a label containing a half-width square bracket —
  previously produced **no edge and no finding**. It is now reported with the
  file and line number, so silent non-edges are structurally impossible.
  Deterministic, no model calls, detected at graph-load time.
- `link-add` now refuses a target whose frontmatter title contains `[` or `]`
  (exit non-zero, nothing written): the title is used verbatim as the link
  label and would reproduce the malformed shape on every future link. Rename
  the title or add the link manually.
- Tests: exactly-one malformed finding with line-number assertion, edge/finding
  count invariance against the baseline fixture, bracket-title refusal
  including the bidirectional validate-before-write path.

### Changed
- `docs/usage.md` / `docs/usage.ja.md`: one-line pointer to `lineage` for
  decision-evolution questions (kept in sync between the two languages).

### Notes
- The optional balanced-bracket label parser from #27 is deliberately **not**
  implemented: with the lint catching every silent non-edge and `link-add`
  refusing bracket titles, the failure mode is closed without growing the link
  grammar. Revisit only if bracketed labels prove genuinely necessary.

## [1.17.0] - 2026-08-15

### Added
- **Typed edges (decision lineage)** in `scripts/kb_graph.py`: the graph now reads
  the `superseded_by:` frontmatter field as a `superseded_by` edge (entries-root-relative,
  the single source for supersede lineage — never duplicated as a list link), plus two
  typed list links — `- amends:` (correction/addendum note that does not rise to a
  replacement) and `- extends:` (elaboration/specialization). `see:`/`ref:` stay
  untyped and fully supported, so existing knowledge bases are unaffected.
- `scripts/kb_graph.py lineage <entry>` — the supersede chain around an entry: what
  it (transitively) replaced, the replacement chain forward, and the **current
  authority** (flagged when not `active`/`draft`). Structure only — IDs, titles,
  status, never body text — built on demand, pure stdlib.
- Four deterministic supersede lint checks: `superseded-status-mismatch`
  (`status:` ⇄ `superseded_by:` must agree), `superseded-broken` (target must
  exist), `superseded-missing-successor` (`status: superseded` needs a
  `superseded_by:`), and `supersede-cycle` (reported once per chain member so
  pre-commit file scoping still catches it). Judgment work — which prose markers
  deserve typing — stays in `/review-knowledge`; lint stays model-free.
- `link-add --kind` now also accepts `amends` and `extends`.
- Tests: supersede-chain fixture (typed edge counts, every new lint finding, cycle
  termination, per-member cycle scoping, typed `link-add`) in
  `tests/test_kb_graph.py`.

### Changed
- `record-knowledge` skill: the Correction Flow's successor-side back-link is now a
  typed `- amends:` link instead of the prose `- see: corrects [original](...)`;
  documented when `amends:`/`extends:` apply versus plain `see:` (typed vocabulary
  kept deliberately minimal).
- `recall-knowledge` skill: a `superseded` search hit is resolved to the current
  authority with `kb_graph.py lineage` (the chain may be multi-step) instead of
  hand-following `superseded_by` links; `lineage` joins the structure-first command
  set for decision-evolution recalls.
- `review-knowledge` skill: the precomputed `graph_lint` input now covers the
  superseded chain check deterministically; multi-step chains are reported via
  `lineage` so the user sees where a superseded entry actually ends up.

## [1.16.0] - 2026-08-11

### Added
- `hooks/sessionend_tasks_mirror.py` — SessionEnd hook that mirrors worktree-local
  `.claude/tasks/` back to the main checkout in issue-centric mode. Copy-only with
  shadow-copy on conflict; no-ops in git-tracked mode, agent worktrees, and outside
  worktrees. Opt-out: `CCMEMO_TASKS_MIRROR=0`. (#21)
- `scripts/kb_graph.py link-add` — deterministic see-link writer: idempotent,
  append-only, atomic, fails loudly on ambiguity; `--bidirectional` validates both
  directions before writing either file. record-knowledge step 7 now calls it
  instead of hand-editing backlinks. (#22)

## [1.15.0] - 2026-07-27

### Added
- `scripts/kb_graph.py` — link-graph CLI over the knowledge base: `stats` (hubs,
  orphans, connected components), `neighborhood` (BFS around an entry), `path`
  (shortest link path), and a deterministic `lint` (broken/out-of-tree/self/duplicate
  links, missing titles, filename format, unregistered tags; exit 1 on findings, so it
  slots into pre-commit). Pure stdlib — no uv, no index, no network. The graph is
  built on demand from `- see:` / `- ref:` links (no persisted index to go stale), and
  query output is structure only (IDs, titles, edge kinds — never body text), so it
  stays cheap to keep in a model context.
- `tests/test_kb_graph.py` — dependency-free self-tests over a synthetic knowledge
  base (link resolution, broken-link vs out-of-tree classification, components, CLI
  JSON output, lint exit codes and file scoping).

### Changed
- `recall-knowledge` skill: multi-hop recalls (tracing how a decision evolved,
  connecting two topics, mapping an area) now query graph structure first via
  `kb_graph.py neighborhood` / `path` and read only the endpoint entries, instead of
  chaining search → read → follow links → read again.
- `review-knowledge` skill: the main agent precomputes `kb_graph.py stats` + `lint`
  (deterministic, ~1s) and passes the output to the review subagent, which spends its
  effort on judgment work (staleness, missing connections, synthesis) instead of
  re-deriving link facts by hand. Skill now allows Bash for that precompute step;
  graceful fallback to the previous read-everything behaviour when `python3` is
  unavailable.

## [1.13.0] - 2026-06-22

### Added
- **Opt-in auto-commit safety net**: a `SessionEnd` hook (`hooks/sessionend_autocommit.py`) plus the existing `PreCompact` hook commit *only* `.claude/knowledge/` and `.claude/tasks/` changes when `CCMEMO_AUTOCOMMIT=1` is set. Off by default; never runs `git add -A`; **never pushes** (push stays a human gate). It complements — does not replace — the manual session-wrap commit, so it is a no-op once you have already committed.
- `hooks/lib/autocommit.py` — shared commit core for both hooks. Gates, in order: opt-in env → inside a git work tree → no merge/rebase/cherry-pick in progress → target pathspec has changes → leak-scan clean. Commit messages carry **no AI-attribution trailers** and list the changed entry names.
- Leak-scan gate reuses `hooks/lib/leak_scan.py`: leak-prone shapes block the commit by default; set `CCMEMO_AUTOCOMMIT_ON_LEAK=warn` to commit with a stderr warning instead.
- `tests/test_autocommit.py` — dependency-free self-tests (opt-in no-op, pathspec scoping, leak block/warn, mid-merge skip, no AI-attribution).

### Changed
- `hooks/hooks.json`: register the `SessionEnd` hook (timeout 30); raise the `PreCompact` timeout 10 → 30 to accommodate the optional commit.
- `hooks/precompact_checkpoint.py`: after saving its checkpoint, performs the opt-in auto-commit (shared `lib/autocommit.py`) and notes the result in its systemMessage.

## [1.12.0] - 2026-06-22

### Added
- **Redact-on-record**: a deterministic PostToolUse guard (`hooks/postwrite_redact_entries.py`) that sanitizes knowledge entries on write, so recording no longer relies on the model remembering to redact. Hybrid behaviour: unambiguous secret *values* are masked in place; leak-prone *shapes* are warned about (not auto-edited). Registered first in the `Write|Edit` chain.
- `hooks/lib/redact.py` — shared redact SPEC (sensitive-key + value-pattern masking: `op://`, JWT, PEM, GitHub token, non-noreply email). The entry-body profile deliberately excludes the high-entropy pattern so it never clobbers git SHAs / long paths / base64 examples.
- `hooks/lib/leak_scan.py` — leak-prone shape detection (UUID, home-path, `${...}` unexpanded placeholders, base64-ish blobs, and private repo names supplied at runtime via `$CCMEMO_PRIVATE_REPO_NAMES` so no private name is baked into this public source).
- `tests/test_policy.py` — dependency-free self-tests for redact & leak-scan.

### Changed
- `hooks/stop_context_guard.py`: the context-size stop guard now blocks **once** so the *model* self-assesses whether the session produced knowledge worth recording. The reason returns to the model (not a yes/no question to the user), which then invokes record-knowledge / session-wrap or ends the session for routine work — avoiding the prior false block on e.g. install-only sessions. `stop_hook_active` guarantees the second stop is allowed.

## [1.11.0] - 2026-06-16

### Added
- `/recall-knowledge` skill: on-demand **hybrid semantic search** over the knowledge base — lexical (ripgrep + mecab) fused with local vector embeddings and the `see:`-link graph (RRF), bridging synonyms and JA-query/EN-identifier gaps that keyword search misses
- `scripts/kb_index.py` — build/refresh a per-machine vector index (sha256 incremental, idempotent)
- `scripts/kb_search.py` — hybrid query (lexical + vector + RRF + `see:` expansion + frontmatter filters) with lazy re-embed of changed entries
- `hooks/post-merge.sample` — consumer-side incremental re-index after `git pull`
- `docs/hybrid-search.md` — setup, usage, and verification (incl. corporate TLS notes)

### Requirements (optional — the feature is opt-in)
- Semantic search needs `uv` (or Python ≥3.10) plus `fastembed` and `sqlite-vec`. The embedding model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (~220 MB) is downloaded **once** and runs fully local — no knowledge text leaves the machine.
- **Graceful fallback**: without the index or these deps, `/recall-knowledge` falls back to ripgrep-only — nothing breaks.
- The vector index (`.claude/knowledge/.index/kb.db`) is a per-machine derived cache and **must not be committed** (gitignore it; see `docs/hybrid-search.gitignore-snippet.txt`).

### Notes
- The per-prompt `userpromptsubmit_knowledge_search.sh` hook is **unchanged** (stays ripgrep — instant, no model load). Semantic search is on-demand only.
- First-time setup / verification covers dependency install, model download, index build, and corporate TLS inspection (`uv --system-certs` + `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`); see `docs/hybrid-search.md`.

## [1.10.2] - 2026-04-16

### Fixed
- Remove Sonnet model pinning from the record-knowledge subagent so it inherits the active session model instead of forcing a fixed model

## [1.10.1] - 2026-04-10

### Removed
- Temporary rollout trace line in `userpromptsubmit_knowledge_search.sh` that appended to `/tmp/ccmemo-knowledge-hook.log` (verification complete)

## [1.10.0] - 2026-04-10

### Added
- UserPromptSubmit hook `userpromptsubmit_knowledge_search.sh` that auto-searches `.claude/knowledge/entries/` with mecab + rg and injects the top 5 hits as `additionalContext` (#46)
- Registered the hook via `hooks/hooks.json` so it loads automatically through `${CLAUDE_PLUGIN_ROOT}`

### Requirements
- `mecab`, `mecab-ipadic-utf8`, `jq`, `rg` must be available on PATH; the hook no-ops silently if any are missing

## [1.9.0] - 2026-04-07

### Changed
- Delegate review-knowledge to Sonnet subagent to minimize main context consumption
- Extract review-knowledge procedure into separate procedure.md
- Change review-knowledge allowed-tools from `Read, Grep, Glob, Edit, Write` to `Read, Agent`

## [1.8.0] - 2026-04-05

### Added
- Delegate record-knowledge to Sonnet subagent to minimize main context consumption (#43, #44)
- Structured prompt template for subagent delegation
- Plan-task subagent delegation support
- Subagent Delegation section in README

## [1.7.0] - 2026-03-31

### Added
- TaskCreate/TaskUpdate sync with plan-task progress tracking (#10)
- `session_state.md` for fast session recovery

## [1.6.0] - 2026-03-28

### Added
- Entry lifecycle management (active/stale/archived) (#22, #23)
- Granularity control for knowledge entries (#27)
- Correction flow for updating existing entries (#28)
- Synthesis support for merging related entries

## [1.5.0] - 2026-03-28

### Added
- Large entry splitting with soft size limits (#8, #29)

## [1.4.0] - 2026-03-28

### Added
- Tag registry auto-maintenance (#26)
- Overview/detail hierarchy for knowledge entries (#25)

## [1.3.0] - 2026-03-28

### Added
- Reference integrity check (#32)
- Issue link recommendation for knowledge entries (#35)

## [1.2.0] - 2026-03-28

### Added
- `YYYY/MM/` directory structure for knowledge entries (#24)
- Unidirectional link detection and auto-fix for tags/links
- SystemMessage logging to PostToolUse hook

### Fixed
- Review-knowledge link completion (#3)
- PostToolUse hook logging (#2)

## [1.1.0] - 2026-03-16

### Added
- Context Guard: three-stage defense against knowledge loss during context compaction
- Context-*.md incremental capture and checkpoint lifecycle
- Issue sync for plan creation, revision, and completion
- Auto-update active tasks after recording knowledge
- Environment-specific recording guidance
- Review-knowledge skill for knowledge base maintenance

### Changed
- Moved task/issue sync responsibility to plan-task

## [1.0.0] - 2026-03-10

### Added
- Record-knowledge skill with automatic see-link discovery
- Plan-task skill with Git-tracked and issue-centric modes
- Tag similarity check to prevent duplicate tags
- Plugin marketplace support via plugin.json
- MIT License

[1.11.0]: https://github.com/LevNas/ccmemo/compare/v1.10.2...v1.11.0
[1.10.2]: https://github.com/LevNas/ccmemo/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/LevNas/ccmemo/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/LevNas/ccmemo/compare/v1.9.0...v1.10.0
[1.9.0]: https://github.com/LevNas/ccmemo/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/LevNas/ccmemo/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/LevNas/ccmemo/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/LevNas/ccmemo/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/LevNas/ccmemo/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/LevNas/ccmemo/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/LevNas/ccmemo/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/LevNas/ccmemo/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/LevNas/ccmemo/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/LevNas/ccmemo/releases/tag/v1.0.0
