# Hybrid Knowledge Search (experimental)

A thin vertical slice that augments ccmemo's filename/`rg` knowledge lookup with a
**local hybrid search**: lexical (ripgrep + mecab) fused with vector similarity
(local embeddings), reinforced by the `see:` link graph.

The Markdown knowledge base (`.claude/knowledge/entries/**/*.md` + frontmatter +
`see:` links) is the source of truth. The search index is a **derived, per-machine
cache** — regenerable at any time and **never committed to git**.

## Scripts

| Script | Purpose |
| --- | --- |
| `scripts/kb_index.py`  | Build / incrementally refresh the index from the Markdown |
| `scripts/kb_search.py` | Query: lexical + vector arms, RRF fusion, `see:` 1-hop expansion, frontmatter filters |
| `hooks/post-merge.sample` | Consumer git hook: incremental re-index after `git pull` |

For structural / multi-hop queries that need no index at all — hubs, orphans,
neighborhoods, shortest link paths, link lint — see the pure-stdlib
`scripts/kb_graph.py` in [link-graph.md](link-graph.md); it complements the
meaning-based retrieval described here.

## Dependencies

- [`fastembed`](https://pypi.org/project/fastembed/) — local ONNX embeddings.
  Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dim,
  multilingual incl. Japanese). Downloads ~220 MB **once** into the fastembed
  cache on first run. Nothing is sent to any external API — embedding is fully
  local, satisfying the secret-management rule that knowledge body text must not
  leave the machine.
- [`sqlite-vec`](https://pypi.org/project/sqlite-vec/) — vector KNN inside SQLite.
- `mecab`, `rg` — already required by the existing knowledge-search hook (the
  lexical arm reuses the same Japanese tokenisation approach).

### Running

The scripts carry PEP 723 inline metadata, so the simplest path is
[`uv`](https://docs.astral.sh/uv/) (already in this project's mise inventory):

```bash
# First run installs fastembed + sqlite-vec into an ephemeral env and downloads
# the model (~100 MB, one time). Subsequent runs are fast.
uv run scripts/kb_index.py  /path/to/.claude/knowledge/entries/
uv run scripts/kb_search.py /path/to/.claude/knowledge/entries/ "クエリ"
```

Or install the two libs into any Python ≥3.10 environment and call `python3`
directly:

```bash
pip install 'fastembed>=0.3' 'sqlite-vec>=0.1.6'
python3 scripts/kb_index.py  /path/to/.claude/knowledge/entries/
python3 scripts/kb_search.py /path/to/.claude/knowledge/entries/ "クエリ"
```

## Indexing

```bash
uv run scripts/kb_index.py ~/proj/.claude/knowledge/entries/
# index: +142 ~0 -0 =0 (… chunks embedded) -> …/.claude/knowledge/.index/kb.db
```

- **Incremental & idempotent**: each entry's raw-text sha256 is stored; only
  changed entries are re-embedded, deleted entries are purged. Re-running with no
  changes embeds nothing.
- **Chunking**: one chunk per entry, plus per-`##`-section chunks for large
  entries (>1200 chars) so long entries stay retrievable section-by-section.
- The DB lives at `<entries>/../.index/kb.db`, i.e.
  `.claude/knowledge/.index/kb.db`.

## Searching

```bash
uv run scripts/kb_search.py ~/proj/.claude/knowledge/entries/ "トークン注入のラッパー" \
  --status active --tag '#secret-management' --top 8
```

Filters: `--status`, `--tag` (repeatable), `--type`, `--created-from`,
`--created-to`. Other flags: `--top N`, `--summary`, `--edges N`,
`--linked-from N`, `--json`, `--no-lazy`, `--no-mecab`.

Pipeline: lexical rank (rg + mecab) and vector rank (sqlite-vec KNN) are each
ranked, fused with **RRF (k=60)**, the top hits are **expanded one hop along
typed links** (`see:` / `ref:` / `amends:` / `extends:`), then frontmatter
filters apply. Output is ranked `path` + score + snippet, plus a `when:` line
when the entry has a frontmatter `description`.

### Neighbourhood summary (`--summary`)

The default output still makes the model open candidates to decide between
them (a body averages ~7 KB). `--summary` prints what is needed to pick **one**
entry without opening any — per hit, the title, the `description` (the entry's
trigger condition: when to open it) and its typed edges with the label each
link carries — in the OKF `index.md` shape:

```
* [Title](2026/09/20260923-143000-user-slug.md) - Open when ... (the description)
  - see 20260727-025736 — why the author linked it (the label)
  - amends 20260815-213327 — what this entry corrects
  - see 20260922-090000 — the upstream context (+5)
```

- Neighbours are identified by the `YYYYMMDD-HHMMSS` filename prefix (unique
  in a ccmemo KB; `kb_graph.py` resolves it as a filename substring, and a
  file glob `*/<id>-*.md` finds the path) plus the link's label — the reason
  to follow it — rather than by their long title. `(+N)` counts edges not
  shown. Entries without a `description` fall back to their lead paragraph,
  marked `(lead)`. A non-active status is flagged, e.g. `(superseded)`.
- Byte budget: on a 288-entry Japanese KB ten summaries with the defaults
  (`--edges 3 --linked-from 0`) measure ~6.9 KB — under one entry body.
  `--linked-from 1` adds the newest incoming link per hit (~90 bytes each);
  useful to find the hub around a leaf. `-1` means all.
- Hub → leaf reading: search for the topic, take the hub's summary, and Read
  the single leaf whose label matches the question. `--edges -1` on a hub
  lists every leaf.
- `--json` carries the same fields uncapped by the text caps: `description`,
  `description_source` (`frontmatter` | `lead`), `edges` / `linked_from`
  (each with `target`/`source`, `rel`, `label`, `title`) and the totals.

The edges come from the index (`edges` table: `src`, `target`, `rel`,
`label`, `ord`), extracted by `hooks/lib/edges.py` from the `- see:`-style
bullets in the body or from a frontmatter `related_docs:` list (design-document
corpora). An index built before this table existed is upgraded in place on
the next search — metadata and edges are re-read from the Markdown, nothing
is re-embedded.

### Replaying search misses (`kb_recall_eval.py`)

Retrieval misses have several independent causes; which one dominates on a
given corpus has to be measured, not guessed. Keep a log of misses as
(query, expected entry) pairs — a Markdown table, e.g.
`.claude/knowledge/search-misses.md`, **outside** the entries directory so it
is never indexed — and replay it before and after any retrieval change:

```bash
uv run scripts/kb_recall_eval.py ROOT .claude/knowledge/search-misses.md            # hit@10
uv run scripts/kb_recall_eval.py ROOT .claude/knowledge/search-misses.md --json > before.json
uv run scripts/kb_recall_eval.py ROOT .claude/knowledge/search-misses.md --compare before.json
```

Table columns: `| date | query | expected | actual top | cause |` — only the
second and third are read; `expected` is a unique filename substring (or
relpath), several alternatives separated by `;`. JSON Lines
(`{"query": ..., "expected": [...]}`) works too.

**Lazy refresh**: before searching, on-disk hashes are compared with the index;
changed/new entries are re-embedded just-in-time (disable with `--no-lazy`).

## Index is not committed (consumer setup)

The index is a per-machine derived cache. Committing the binary DB across machines
or teammates would create unmergeable conflicts. Add to your project `.gitignore`:

```gitignore
.claude/knowledge/.index/
```

To keep it fresh after pulling teammates' new entries, install the post-merge
hook:

```bash
cp path/to/ccmemo/hooks/post-merge.sample .git/hooks/post-merge
chmod +x .git/hooks/post-merge
# adjust CCMEMO_KB_ROOT / CCMEMO_KB_INDEX env vars if your layout differs
```

## NixOS

On NixOS, numpy's manylinux wheel cannot resolve `libstdc++.so.6` when the
scripts run under the Nix-native CPython (nix-ld's `NIX_LD_LIBRARY_PATH` is not
consulted for it, because that interpreter does not go through the nix-ld shim).
Both scripts detect this at startup and re-exec themselves once with
`LD_LIBRARY_PATH` pointing at gcc's libstdc++ directory
(`gcc -print-file-name=libstdc++.so.6`), so no manual setup is needed as long
as `gcc` is on `PATH`. See issue #13 for the full analysis.

## Status

Experimental vertical slice. It is **not wired into**
`hooks/userpromptsubmit_knowledge_search.sh` yet — that hook still uses the
existing rg-only lexical search. Wiring is deferred until this slice is validated
against the rg baseline.
