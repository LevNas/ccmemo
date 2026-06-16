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
`--created-to`. Other flags: `--top N`, `--json`, `--no-lazy`, `--no-mecab`.

Pipeline: lexical rank (rg + mecab) and vector rank (sqlite-vec KNN) are each
ranked, fused with **RRF (k=60)**, the top hits are **expanded one hop along
`see:` links**, then frontmatter filters apply. Output is ranked `path` + score +
snippet.

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

## Status

Experimental vertical slice. It is **not wired into**
`hooks/userpromptsubmit_knowledge_search.sh` yet — that hook still uses the
existing rg-only lexical search. Wiring is deferred until this slice is validated
against the rg baseline.
