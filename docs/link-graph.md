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
python3 scripts/kb_graph.py supersede <old> <new> --reason "what changed"  # change flow
python3 scripts/kb_graph.py lint                     # exit 1 on findings
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
so the caller can fall back to a manual edit. `--kind see|ref|amends|extends`
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

- `--json` — machine-readable output for every subcommand
- `--root <dir>` — entries root (default `.claude/knowledge/entries`)
- `--depth <n>` — BFS depth for `neighborhood` (default 1)
- `--registry <file>` — tag registry for `lint` (default `<root>/../CLAUDE.md`)

## How the skills use it

- **`/record-knowledge`** — backlinks (step 7) and typed links are written with
  `link-add` instead of hand-editing entry files; the Change Flow's
  frontmatter pair, banner and successor-side `amends:` back-link are applied
  with `supersede`.
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
  with the reviewer.

## Related

- [hybrid-search.md](hybrid-search.md) — meaning-based retrieval (needs the
  per-machine vector index); complements the graph's structure-only queries
- [architecture.md](architecture.md) — how the three `kb_*` scripts and the
  skills fit together
