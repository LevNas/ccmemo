# Link-Graph CLI (`kb_graph.py`)

`scripts/kb_graph.py` answers structural questions about the knowledge base —
what connects to what, and where the link integrity is broken. Pure stdlib: it
runs with plain `python3`, needs no `uv`, no vector index, and no network.

## Design

- **On-demand graph, no persisted index.** The graph is rebuilt each run from
  the `- see:` / `- ref:` list links in the entries (~1s for a few hundred
  entries). There is nothing to go stale and nothing to rebuild after a pull.
- **Structure only, never body text.** Output contains entry IDs, titles, and
  edge kinds — so results stay cheap to inject into a model context. The
  intended flow is *structure first, bodies last*: use `neighborhood` / `path`
  to plan where to go, then read only the endpoint entries.
- **Deterministic lint.** `lint` makes no model calls, so it can gate commits.
  Judgment work — staleness review, missing-connection suggestions — stays in
  `/review-knowledge`.

## Subcommands

Run from the project root; the default `--root` is `.claude/knowledge/entries`.

```bash
python3 scripts/kb_graph.py stats                    # hubs, orphans, components
python3 scripts/kb_graph.py neighborhood <entry> --depth 2
python3 scripts/kb_graph.py path <entry-a> <entry-b> # shortest link path
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

### `lint [files...]`

Deterministic integrity checks; exits 1 when there are findings, 0 when clean:

| Check | Meaning |
|---|---|
| `broken-link` | `see:`/`ref:` target resolves to no file inside the repository |
| `out-of-tree` | target escapes the repository — resolves differently per checkout or machine |
| `self-link` | entry links to itself |
| `duplicate-link` | same link listed more than once in an entry |
| `missing-title` | no `title:` in the frontmatter |
| `filename` | filename does not match `<date>-<time>-...-<slug>.md` |
| `unknown-tag` | tag not in the registry (default: `<root>/../CLAUDE.md`, override with `--registry`) |

Passing file arguments limits the *reported* findings to those files (the
graph is still built from all entries), which is exactly what a pre-commit
hook wants:

```sh
changed=$(git diff --cached --name-only -- .claude/knowledge/entries/)
[ -z "$changed" ] || python3 scripts/kb_graph.py lint $changed
```

## Addressing entries

`neighborhood` and `path` accept an entry's path relative to the entries root,
or any **unique filename substring** — `docker-compose` is enough if only one
entry matches. Ambiguous queries fail with the list of candidates.

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

- **`/recall-knowledge`** — multi-hop recalls (tracing how a decision evolved,
  connecting two topics, mapping an area) query `neighborhood` / `path` first
  and read only the endpoint entries, instead of chaining
  search → read → follow links → read again.
- **`/review-knowledge`** — the main agent precomputes `stats` + `lint` and
  passes the output to the review subagent, which spends its effort on
  judgment work instead of re-deriving link facts by hand.

## Related

- [hybrid-search.md](hybrid-search.md) — meaning-based retrieval (needs the
  per-machine vector index); complements the graph's structure-only queries
- [architecture.md](architecture.md) — how the three `kb_*` scripts and the
  skills fit together
