# Link-Graph CLI (`kb_graph.py`)

`scripts/kb_graph.py` answers structural questions about the knowledge base —
what connects to what, and where the link integrity is broken. Pure stdlib: it
runs with plain `python3`, needs no `uv`, no vector index, and no network.

## Design

- **On-demand graph, no persisted index.** The graph is rebuilt each run from
  the list links in the entries (`- see:` / `- ref:` / `- amends:` /
  `- extends:`) plus the `superseded_by:` frontmatter field (~1s for a few
  hundred entries). There is nothing to go stale and nothing to rebuild after
  a pull.
- **Structure only, never body text.** Output contains entry IDs, titles, and
  edge kinds — so results stay cheap to inject into a model context. The
  intended flow is *structure first, bodies last*: use `neighborhood` / `path`
  to plan where to go, then read only the endpoint entries.
- **Deterministic lint.** `lint` makes no model calls, so it can gate commits.
  Judgment work — staleness review, missing-connection suggestions — stays in
  `/review-knowledge`.

## Edge kinds

| Kind | Source | Meaning |
|---|---|---|
| `see` / `ref` | list link | Untyped association (unchanged, backward compatible) |
| `amends` | list link | Correction / addendum note — corrects part of the target without replacing it |
| `extends` | list link | Elaboration — develops or specializes the target |
| `superseded_by` | `superseded_by:` frontmatter | Full replacement: old entry → its replacement. Single source for supersede lineage — never duplicated as a list link. Entries-root-relative path, at most one per entry |

## Subcommands

Run from the project root; the default `--root` is `.claude/knowledge/entries`.

```bash
python3 scripts/kb_graph.py stats                    # hubs, orphans, components
python3 scripts/kb_graph.py neighborhood <entry> --depth 2
python3 scripts/kb_graph.py path <entry-a> <entry-b> # shortest link path
python3 scripts/kb_graph.py lineage <entry>          # supersede chain → current authority
python3 scripts/kb_graph.py link-add <src> <dst> --reason "why"  # deterministic writer
uv run --with sqlite-vec scripts/kb_graph.py near-pairs --cross-kind  # from the search index
python3 scripts/kb_graph.py supersede <old> <new> --reason "what changed"  # change flow
python3 scripts/kb_graph.py lint                     # exit 1 on findings
python3 scripts/kb_graph.py migrate --to 3           # add id / generated where missing (idempotent)
python3 scripts/kb_graph.py verify <entry> --by human:alice   # record an independent check
python3 scripts/kb_graph.py rename <entry> <new-slug>         # slug only; links follow
python3 scripts/kb_graph.py relink                   # repair links to paths the index saw move
python3 scripts/kb_graph.py index-md --out index.md  # OKF-style table of contents
python3 scripts/kb_graph.py union-recover <file>     # lossless union of diverged append-only copies
```

### `stats`

Graph overview: node/edge counts by edge kind, connected components, the top
hub entries (in/out degree), and orphans (no links in either direction).

### `neighborhood <entry>`

BFS around an entry up to `--depth` (default 1), listing each neighbor with
its depth, link direction (`→` outgoing / `←` incoming), and edge kind.

### `path <a> <b>`

Shortest link path between two entries, treating links as bidirectional.
Exits 1 if no path exists.

### `lineage <entry>`

The supersede chain around an entry, built from `superseded_by:` frontmatter
edges only: what the entry (transitively) replaced, the replacement chain
forward, and the **current authority** — the newest entry in the chain,
flagged when its status is not `active`/`draft`. Structure only (IDs, titles,
status), so a superseded search hit resolves to the entry that actually holds
the current answer in one command.

### `link-add <src> <dst> --reason "..."`

Deterministic writer counterpart of the graph reader: the model decides which
entries to connect and writes the reason; the mechanical edit is deterministic.
Appends after the entry's last link line (or a `## 関連` heading), is
idempotent per target, writes atomically, and exits non-zero on any ambiguity
so the caller can fall back to a manual edit — which must keep the same line
shape, `- see: [<target's exact frontmatter title>](YYYY/MM/<filename>.md) —
<relationship>`: entries/-relative, never `../`. `--kind see|ref|amends|extends`
(default `see`); `--bidirectional` validates both directions before writing
either file; `--dry-run` prints the planned insertion. A target whose
frontmatter title contains a square bracket is refused — the label would not
parse back as a link (see `malformed-link`); rename the title instead.

### `supersede <old> <new> --reason "..."`

The Change Flow in one validated step: marks `old` as replaced by `new`. Sets
the `status: superseded` + `superseded_by:` frontmatter pair on the old entry,
inserts a body-top warning banner
(`> **⚠ superseded (date)** — current: [title](id)`, `--date` overrides
today), and appends an `- amends:` back-link to the replacement via the
`link-add` machinery (skipped when the replacement already links the old
entry). Every piece is validated before anything is written; the command is
idempotent, so re-running completes an interrupted run and a fully applied
state is reported as `already superseded`. Refused loudly: self-supersede, a
conflicting existing successor, a supersede cycle, a bracketed replacement
title, and a replacement with no link anchor (`--dry-run` previews). The
banner is a blockquote on purpose — a list-form line would double-book the
lineage as a graph edge.

### `index-md [--out FILE]`

Writes the whole KB as an OKF-style `index.md`: one bullet per entry,
`* [Title](relpath) - description`, ordered by relpath (i.e. by date), with a
non-active status flagged (`(superseded)`). The description is the entry's
frontmatter trigger condition, so the file doubles as a progressive-disclosure
table of contents that tools other than ccmemo can read; entries without a
description are listed with the title only, and the count of those is
printed, so the gap stays visible. Stdout when `--out` is omitted. The same
shape is what `kb_search.py --summary` prints per hit (see
[hybrid-search.md](hybrid-search.md)).

### `migrate --to 3 [--by ACTOR] [--tz +HH:MM] [--dry-run]`

Brings every entry up to schema 3 and changes nothing else: inserts
`id: <uuid4>` (after `title:`) where missing and `generated: {by, at}` (after
`created:`) where missing — `by` is `--by` (default `claude-code`), `at` the
filename's date-time in the corpus time zone (`--tz`, default: this
machine's). Idempotent: a second run reports 0 changes. `verified` is never
backfilled — nobody has independently checked the old entries, so they start
unverified — and `confidence:` is left in place (retired, ignored). Entries
without frontmatter are skipped and named. Steps around it:
[upgrading.md](upgrading.md#127-entry-ids-and-verification).

### `verify <entry> --by ACTOR [--at ISO-8601] [--dry-run]`

Appends one `{by, at}` event to the entry's `verified:` list (created after
`generated:` when absent; `--at` defaults to now with the local offset).
`ACTOR` is `human:<handle>`, `claude-code[/<model-id>]` or `process:<name>`;
anything else exits non-zero without writing. The same event twice is a
no-op. The command prints the resulting tier; an event older than
`generated.at` is recorded but noted as expired. Use it when a person, an
independent agent session or a gate process actually checked the content —
not for lint passing, link fixes or banner edits.

### `rename <entry> <new-slug> [--dry-run]`

Changes the slug only: the `YYYYMMDD-HHMMSS` prefix (the creation time, a
fact), the author segment and the `YYYY/MM/` directory stay. Every link and
`superseded_by:` in the corpus that resolves to the entry is rewritten to the
new path, keeping each link's style (root-relative stays root-relative,
directory-relative stays directory-relative). `id` carries the identity
across. Refuses a slug that is not kebab-case, a target that exists, or a
file not named `<date>-<time>-<author>-<slug>.md`.

### `relink [--dry-run]`

Repairs links to paths the search index recorded as **moved**: when a
re-index (`kb_index.py`, or the lazy refresh before a search) finds a known
`id` at a new relpath while the old file is gone — a manual `mv`, a rename
done without `rename` — it re-keys the rows (no re-embedding) and writes the
pair to a `moves` table. `relink` reads that table and rewrites every link
and `superseded_by:` still pointing at an old path, in recorded order, so
chains resolve hop by hop. Needs an index; prints `nothing to relink` when
there is nothing left.

### `lint [files...]`

Deterministic integrity checks; exits 1 when there are findings, 0 when clean:

| Check | Meaning |
|---|---|
| `malformed-link` | line looks like a link (`- see: [` …) but does not parse — e.g. a label containing a square bracket — and would otherwise silently produce no edge; reported with the line number |
| `broken-link` | `see:`/`ref:` target resolves to no file inside the repository |
| `out-of-tree` | target escapes the repository — resolves differently per checkout or machine |
| `self-link` | entry links to itself |
| `duplicate-link` | same link listed more than once in an entry |
| `missing-title` | no `title:` in the frontmatter |
| `filename` | filename does not match `<date>-<time>-...-<slug>.md` |
| `unknown-tag` | tag not in the registry (default: `<root>/../CLAUDE.md`, override with `--registry`) |
| `superseded-status-mismatch` | `superseded_by:` present but `status:` is not `superseded` |
| `superseded-broken` | `superseded_by:` target resolves to no entry |
| `superseded-missing-successor` | `status: superseded` but no `superseded_by:` |
| `supersede-cycle` | `superseded_by:` chain loops — reported once per member so file scoping still catches it |
| `missing-description` | no `description:` — the trigger condition (when to open the entry) that search summaries and the prompt hook show |
| `description-length` | description under 80 chars (cannot name a situation) or over 320 (reads as a summary) |
| `unlabeled-link` | a `see:`/`ref:`/`amends:`/`extends:` line with nothing after the link — the "— why to follow it" label is what lets a reader decide without opening the target |
| `amends-unreciprocated` / `extends-unreciprocated` | the target of a correction / elaboration does not link back to it (any link kind) and is not superseded by it — readers of the target would never learn of the correction |
| `missing-id` / `duplicate-id` | no `id:` (uuid4), or the same `id` in two entries of this corpus (the same `id` in *another* corpus is a mirror, not a finding) |
| `missing-generated` | no `generated:` — or not a `{by, at}` mapping with an ISO 8601 `at` |
| `invalid-actor` | `generated.by` or a `verified[].by` is not `human:<handle>`, `claude-code[/<model-id>]` or `process:<name>` |
| `verification-expired` | the latest `verified.at` is older than `generated.at`: the body was rewritten since it was checked (informational) |
| `stale-after-passed` | `stale_after:` is behind today (informational) |
| `duplicate-title` | two entries share a title — hard to tell apart in search results (informational) |
| `divergent-mirror` | the same `id` at several paths in the search index (a mirror in another corpus, or a copy) with different content: the copy that differs from the knowledge-base one is named; an exact mirror is silent (informational; needs an index built with `scope: repo` to see other corpora) |

**Schema-gated checks.** Checks are enforced only when the knowledge base
declares the schema that introduced them in the frontmatter of
`<root>/../CLAUDE.md` (the scaffolded `CLAUDE.md` declares the latest):
`missing-description` … `extends-unreciprocated` from `schema_version: 2`,
`missing-id` … `invalid-actor` from `schema_version: 3`. On a corpus that
declares less they are still listed, under an *advisory* heading, but do not
affect the exit code — so updating the plugin never turns a pre-commit lint
red before the corpus is migrated. The three *informational* rows are
advisory at every schema: they describe the state of the knowledge, not a
broken convention. `--schema N` lints as if the corpus declared N (`--json`
output carries `severity`: `error` | `advisory`); `CCMEMO_SCHEMA_VERSION`
does the same for a shell. Migration steps: [upgrading.md](upgrading.md).

The post-write hook `hooks/postwrite_kb_lint.py` runs `lint <file>` on every
knowledge entry a Write/Edit touches and returns the findings as a warning in
the same turn (advisory — a write cannot be undone — but the entry is still in
context, so the fix is one edit away). The conventions are owned by ccmemo;
another plugin or a merge gate wanting the same checks calls this CLI instead
of re-implementing them.

Passing file arguments limits the *reported* findings to those files (the
graph is still built from all entries), which is exactly what a pre-commit
hook wants:

```sh
changed=$(git diff --cached --name-only -- .claude/knowledge/entries/)
[ -z "$changed" ] || python3 scripts/kb_graph.py lint $changed
```

That snippet resolves `scripts/kb_graph.py` and therefore only works inside
this repository — a consuming repository needs one of the wirings below.

### Wiring the lint into a consuming repository

A git hook runs outside Claude Code, and the plugin cache path is
version-keyed (`…/plugins/cache/<marketplace>/ccmemo/<version>/scripts/kb_graph.py`).
Do **not** point a hook at that path: after every plugin update it keeps
running the old frozen copy (old versions stay in the cache, so nothing
errors), and when the cache is eventually cleared the hook dies — silently,
if the hook skips on a missing script. Neither failure announces itself.

**Recommended — commit a copy into the repository.** Copy
`scripts/kb_graph.py` from the plugin into the repo at any stable path,
point the hook at it, and update the copy deliberately when a release
changes lint behaviour (release notes call that out). Every clone and
machine then gets the same lint with the repo, plugin installed or not:

```sh
#!/bin/sh
changed=$(git diff --cached --name-only --diff-filter=d -- .claude/knowledge/entries/)
[ -z "$changed" ] && exit 0
command -v python3 >/dev/null 2>&1 || { echo "kb-lint: python3 not found, skipping" >&2; exit 0; }
lint=tools/kb_graph.py   # repo-committed copy
[ -f "$lint" ] || { echo "kb-lint: $lint not found, skipping" >&2; exit 0; }
exec python3 "$lint" lint $changed
```

Keep the skip paths loud (write to stderr): a gate that skips silently is the
worst failure mode — nothing lints and nothing tells you.

**Alternative — resolve the newest cache copy, fail loudly.** To track the
plugin automatically instead, glob the cache for the newest version and
refuse to continue when none is found (rather than skipping):

```sh
lint=$(ls -d "$HOME"/.claude/plugins/cache/*/ccmemo/*/scripts/kb_graph.py 2>/dev/null | sort -V | tail -1)
[ -n "$lint" ] || { echo "kb-lint: no plugin cache copy found — install ccmemo or commit a copy" >&2; exit 1; }
exec python3 "$lint" lint $changed
```

### `near-pairs [--kind K[,K]] [--cross-kind] [--top N] [--threshold T]`

The one subcommand that reads the **search index** rather than the files:
the closest document pairs by cosine similarity of their whole-document
embeddings, deterministic, structure only. Each pair is printed with its
similarity, `linked` or `unlinked` (an edge in either direction exists in the
index's `edges` table), and `same-content` / `same-id` when the two are a
byte-identical mirror or share an entry `id`:

```
0.9137  unlinked                [kb] 2026/09/20260901-000003-user-gamma.md
                                [docs] docs/guide.md
```

Read it as two lists: *duplicate candidates* (high similarity, unlinked, not
a known mirror) and *neighbours without a link* (two documents that say
related things and do not point at each other). The judgement — merge, link,
or leave — stays with `/review-knowledge`; this command only surfaces the
pairs. `--kind kb,docs` restricts the documents considered, `--cross-kind`
keeps only pairs whose kinds differ (the kb–docs axis), `--threshold`
drops pairs below a similarity, `--top 0` prints all. `--json` gives the
fields. Paths are the index's keys (repository-relative under `scope: repo`).
Because the vector table is a loadable SQLite extension, this subcommand
needs the `sqlite_vec` module: `uv run --with sqlite-vec scripts/kb_graph.py
near-pairs`. Every other subcommand stays plain `python3`.

### `union-recover <file> [--theirs <ref>]`

Recovery helper for git-tracked mode with several checkouts (issue #24): the
same capture file (`context-*.md`) or the `see:` block of a hub entry gets
appended to in two checkouts and the copies diverge. For append-only
divergence a union is exact, and this subcommand automates it with the
verification built in. It works on a file path, not an entry query, needs no
entry graph (a repository with captures but no knowledge base is fine), and
ignores `--root` / `--json`.

Two situations:

```bash
# 1. a merge/rebase stopped on the file — uses index stages 1/2/3
python3 scripts/kb_graph.py union-recover <file>
git add <file> && git rebase --continue        # or: git merge --continue

# 2. `git pull` refuses: uncommitted local changes vs the fetched ref
#    (common ancestor = merge-base of HEAD and the ref)
git fetch
python3 scripts/kb_graph.py union-recover <file> --theirs origin/main
```

Safety properties:

- **Append-only or nothing.** Both sides must contain every line of the common
  ancestor, in order — zero deleted or rewritten lines. Otherwise nothing is
  written, the exit code is non-zero, and the message names the side and the
  first ancestor line that was lost. A missing common ancestor (the file was
  added on both sides) is refused the same way.
- **No line lost.** After the union, the ancestor and both sides are each
  verified to be fully contained in the result, in their original order.
- **`--dry-run`** runs every check and reports without writing (still
  non-zero on a refusal).
- **Backup first.** The previous working-tree file is copied to
  `<git-dir>/ccmemo-union-recover/<timestamp>-<filename>` before the atomic
  write — inside the git dir, so it can never be committed.
- The index is never touched: staging and continuing the merge/rebase stay
  with you.

Line order: additions of the upstream side come first (stage 2 during a
rebase, the `--theirs` ref otherwise), local additions after — the same result
as the manual `git merge-file --union` procedure. When one side's addition at
a position wholly contains the other's, it is kept once, so running the
command on an already-unioned file changes nothing. That matters for
situation 2: after committing the union, `git pull --rebase` still stops on
the file (git does not recognize that one side already contains the other) —
run `union-recover <file>` again, `git add`, continue. If the local appends
are not precious as *uncommitted* changes, committing them first and going
straight through situation 1 is the shorter path.

**Knowledge entries.** Only `see:`-style appends are union-safe. Frontmatter
rewrites (`updated:`, `status:`) and body corrections are edit-vs-edit: the
append-only verification rejects them by construction — pick a side by hand
there. Partially overlapping additions are kept from both sides, so a link
added in both checkouts with different reasons survives twice; that is left
to `lint`, which reports it as `duplicate-link`. Run `lint` after recovering
an entry.

## Addressing entries

Every subcommand that takes an entry accepts its path relative to the entries
root, or any **unique filename substring** — `docker-compose` is enough if only
one entry matches. Ambiguous queries fail with the list of candidates.

## Link resolution

Link targets are tried relative to the entries root first, then relative to
the linking entry's own directory. `http(s)` targets are skipped. Links that
leave the entries tree (e.g. into `docs/` or rules files) are existence-checked
by `lint` but are not part of the entry graph.

## Flags

- `--json` — machine-readable output for every graph subcommand
  (`union-recover` prints plain text only)
- `--root <dir>` — entries root (default `.claude/knowledge/entries`)
- `--depth <n>` — BFS depth for `neighborhood` (default 1)
- `--registry <file>` — tag registry for `lint` (default `<root>/../CLAUDE.md`)

## How the skills use it

- **`/record-knowledge`** — backlinks (step 7) and typed links are written with
  `link-add` instead of hand-editing entry files; the Change Flow's
  frontmatter pair, banner and successor-side `amends:` back-link are applied
  with `supersede`. Both procedures restate the hand-written line format for
  the fallback (non-zero exit, or an agent without a shell tool), and step 8
  runs `lint` on the result — an agent that cannot run it says "lint not run"
  in its summary so the caller does.
- **`/recall-knowledge`** — multi-hop recalls (tracing how a decision evolved,
  connecting two topics, mapping an area) query `neighborhood` / `path` first
  and read only the endpoint entries, instead of chaining
  search → read → follow links → read again. A `superseded` hit is resolved to
  the current authority with `lineage`.
- **`/review-knowledge`** — the main agent precomputes `stats` + `lint` and
  passes the output to the review subagent, which spends its effort on
  judgment work instead of re-deriving link facts by hand. The supersede-chain
  part of the health check is covered by the four deterministic lint checks;
  typing judgment (which prose markers deserve `amends:`/`extends:`) stays
  with the reviewer. In `fix` mode a confirmed-current entry is recorded with
  `verify` (human actor when the user confirmed, agent actor when an
  independent session did), starting from the `verification-expired` /
  `stale-after-passed` advisories; moved entries are repaired with `relink`.

## Related

- [hybrid-search.md](hybrid-search.md) — meaning-based retrieval (needs the
  per-machine vector index); complements the graph's structure-only queries
- [architecture.md](architecture.md) — how the three `kb_*` scripts and the
  skills fit together
