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
  `.claude/knowledge/.index/kb.db` — **of the main checkout**. The location is
  resolved through `git rev-parse --git-common-dir`, so a linked worktree
  (`git worktree add`) uses the main checkout's index rather than building
  its own; see [Worktrees](#worktrees-one-index-read-only-from-linked-worktrees).
  `CCMEMO_KB_INDEX=/abs/path/kb.db` overrides the location.

## Searching

```bash
uv run scripts/kb_search.py ~/proj/.claude/knowledge/entries/ "トークン注入のラッパー" \
  --status active --tag '#secret-management' --top 8
```

Filters: `--status`, `--tag` (repeatable), `--type`, `--created-from`,
`--created-to`, `--verified machine|human` (only hits whose latest
independent check is at least that tier; opt-in, never affects ranking),
`--kind` (repeatable; the corpus a hit belongs to — `kb` for the knowledge
base, `docs` or a kind the repository declares — see
[Multi-corpus index](#multi-corpus-index-scope-repo)).
Other flags: `--top N`, `--summary`, `--edges N`, `--linked-from N`,
`--json`, `--no-lazy`, `--no-mecab`.

Pipeline: lexical rank (rg + mecab) and vector rank (sqlite-vec KNN) are each
ranked, fused with **RRF (k=60)**, the top hits are **expanded one hop along
typed links** (`see:` / `ref:` / `amends:` / `extends:`), then frontmatter
filters apply. Output is ranked `path` + score + snippet, plus a `when:` line
when the entry has a frontmatter `description`. Every hit carries its kind
in brackets after the title (`[kb]`). Hits with identical content (same
sha256) or the same entry `id` are **folded** into one line; the other
locations follow on an `also:` line, marked `divergent` when a same-`id`
copy has different content. Equal scores are ordered by path, so a query
gives the same list every run (before 1.28 the order of ties followed
ripgrep's thread scheduling and could change between runs).

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

- Neighbours are identified by a short **handle** plus the link's label — the
  reason to follow it — rather than by their long title. The handle is the
  shortest name that is unique in the corpus: the `YYYYMMDD-HHMMSS` filename
  prefix for almost every entry, the full filename when two entries were
  recorded in the same second (they share the prefix), the relpath when even
  the filename repeats (non-dated corpora). The index computes it, so the
  printed handle is always a valid unique substring for `kb_graph.py
  neighborhood <handle>` and for a file glob `*/<handle>*`. `(+N)` counts edges not
  shown. Entries without a `description` fall back to their lead paragraph,
  marked `(lead)`. A non-active status is flagged, e.g. `(superseded)`. A
  verified hit carries its trust tier as one word after the title —
  `[human]` or `[machine]`, from the latest `verified` event that is not
  older than `generated.at`; unverified hits (most of a corpus) carry nothing.
  The entry `id` is not printed (byte budget); `--json` has it.
- Byte budget: on a 288-entry Japanese KB ten summaries with the defaults
  (`--edges 3 --linked-from 0`) measure ~6.9 KB — under one entry body.
  `--linked-from 1` adds the newest incoming link per hit (~90 bytes each);
  useful to find the hub around a leaf. `-1` means all.
- Hub → leaf reading: search for the topic, take the hub's summary, and Read
  the single leaf whose label matches the question. `--edges -1` on a hub
  lists every leaf.
- `--json` carries the same fields uncapped by the text caps: `description`,
  `description_source` (`frontmatter` | `lead`), `edges` / `linked_from`
  (each with `target`/`source`, `rel`, `label`, `title`, `handle`) and the
  totals, plus the identity and trust fields `id`, `generated` (`{by, at}` or
  null), `verified_tier` (`""` | `machine` | `human`) and `verified_at`.

The edges come from the index (`edges` table: `src`, `target`, `rel`,
`label`, `ord`), extracted by `hooks/lib/edges.py` from the `- see:`-style
bullets in the body or from a frontmatter `related_docs:` list (design-document
corpora). An index built before this table existed is upgraded in place on
the next search — metadata and edges are re-read from the Markdown, nothing
is re-embedded. The same metadata-only upgrade adds the schema-3 columns
(`id`, `generated_by`, `generated_at`, `verified_tier`, `verified_at`).

The index also tracks **moves**: when a re-index finds a known `id` at a new
relpath while the old file is gone, the rows are re-keyed (embeddings kept)
and the old → new pair is written to a `moves` table, which
`kb_graph.py relink` reads to repair links that still point at the old path.
Copies (same `id`, old file still present) are not moves.

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
A **SessionStart hook** (`hooks/sessionstart_index_prewarm.py`) starts the
same incremental refresh in the background when a session begins — detached,
`nice`d, in the main checkout only, and only when an index already exists (an
index is built explicitly, never as a side effect of starting a session). It
runs `uv run --no-project scripts/kb_index.py`, so `uv` never picks up — or
syncs, unattended — a `pyproject.toml` of the repository above the knowledge
base. A lock file, `.index/prewarm.lock`, stops two refreshes from
overlapping; a lock whose pid is dead or that is older than six hours is
ignored (a refresh never runs that long — the pid was reused after a crash or
a reboot). If a session start seems to skip the refresh, remove it by hand:

```bash
rm .claude/knowledge/.index/prewarm.lock
```

Output goes to `.index/prewarm.log`, which is truncated once it passes
512 KB. Lazy refresh remains the correctness layer; the hook only moves the
cost earlier. Opt out with `CCMEMO_INDEX_PREWARM=0`.

## Multi-corpus index (`scope: repo`)

By default the index covers the knowledge base only (`scope: kb`): the
behaviour and latency of every version before 1.28, and nothing to configure.
A repository can opt in to indexing **every document it contains** by
committing `.claude/ccmemo.json`:

```json
{
  "index": {
    "scope": "repo"
  }
}
```

That is the whole opt-in. The candidate set is then what git knows under the
repository root — tracked files plus untracked files that are not ignored —
so `.gitignore` decides what stays out (the index itself, secrets, build
output) and every linked worktree sees the same set. Nested repositories and
worktrees under `.claude/worktrees/` are not descended into. Source code is
out of scope by design (that is a language server's job): only files with a
document extension are indexed — `.md`, `.markdown`, `.txt`, `.rst`.

**git decides the set, and the fallback is closed.** When the checkout has a
`.git` but git cannot answer (not on `PATH`, a sandboxed subagent, a
transient failure), the run does not walk the tree — that would drop the
`.gitignore` protection silently. It prints one line on stderr, indexes the
knowledge base only for that run, and removes nothing from the index; the
other documents are picked up again on the next run where git answers. Only
a directory with no `.git` at all (a `.claude/ccmemo.json` outside any
repository) is walked, and the warning there says that no ignore rules
apply — `exclude` globs still do.

**Untracked files are candidates until they are ignored.** "Untracked but
not ignored" is what keeps a just-written entry searchable before it is
committed, and it applies to every document in the repository: a scratch
`notes.md` or a `credentials.md` dropped next to the code is embedded on the
next refresh and can surface in snippets and in `--summary` leads until it is
ignored or excluded. Commit a `.gitignore` for scratch directories, and use
`exclude` globs (below) for drafts you keep in the tree; both act on the
next refresh, and `.gitignore` also protects every clone and worktree.

`CLAUDE.md` files are instructions to the harness, not documents, and are
never indexed at any scope — the knowledge base's own `CLAUDE.md` was
excluded before 1.28 and the same rule applies to every `CLAUDE.md` in the
repository. `.claude/rules/*.md` and other Markdown under `.claude/` are
ordinary documents; add `".claude/rules/**"` to `exclude` if you do not want
them searchable.

Each indexed file has a **kind**. With no further configuration there are
two: `kb` (the knowledge base root, `.claude/knowledge/entries`) and `docs`
(everything else). The conventions a file follows — frontmatter or none, a
`related_docs:` list, `- see:` lines, plain inline Markdown links — are
detected **per file** by the edge extractors, never assigned by path, so a
corpus where only half the files have frontmatter indexes without any rule.
A file without a frontmatter `title` takes its first `#` heading, then its
filename. Ranking never weights by kind; `--kind` filters. The
`/recall-knowledge` prompt hook is unchanged (knowledge base only, ripgrep
only).

The remaining keys are optional refinements a repository makes to describe
its own layout. ccmemo bakes in no directory names; the names below are
placeholders:

```json
{
  "index": {
    "scope": "repo",
    "extensions": [".md", ".markdown", ".txt", ".rst"],
    "include": ["notes/inventory/*.yaml"],
    "exclude": ["drafts/**"],
    "max_file_bytes": 262144,
    "corpora": [
      { "kind": "notes", "path": "notes", "conventions": "plain" }
    ]
  }
}
```

| Key | Meaning | Default |
|---|---|---|
| `scope` | `kb` (knowledge base only) or `repo` (every document git knows) | `kb` |
| `extensions` | file extensions that count as documents | `.md .markdown .txt .rst` |
| `include` | extra path globs to index regardless of extension (a YAML ledger, say) | none |
| `exclude` | path globs to leave out, **added to** the two defaults: `.claude/tasks/**/context-*.md` (session captures) and `**/.index/**` | the two defaults |
| `max_file_bytes` | files larger than this are indexed with metadata only — title, path, sha256 — and no embedding; the lexical arm still reads them, so a unique token still finds them | `262144` (256 KB) |
| `corpora` | `{kind, path, conventions}` rules: the longest matching `path` prefix decides the kind of a file. `kind: kb` is always the knowledge base root and cannot be moved. `conventions` is `kb` or `plain` | the knowledge base root as `kb`; everything else `docs` / `plain` |

Globs are matched against repository-relative paths; `*` and `?` stay inside
one path segment, `**` spans segments. `conventions` names the *vocabulary* a
corpus writes its frontmatter in: under `plain`, `status: current` satisfies
`--status active` (design-document corpora write `current` where the
knowledge base writes `active`); knowledge-base semantics never move.
`CCMEMO_INDEX_SCOPE=kb|repo` overrides the scope from the environment. A
malformed file is reported on stderr and treated as absent.

Switching the scope re-keys the index (`scope: kb` keys rows by
knowledge-root relpath, `scope: repo` by repository relpath): knowledge
entries keep their embeddings across the switch, the other documents are
embedded on the first `scope: repo` build — which is why the SessionStart
hook above exists. Two review helpers read the multi-corpus index:
`kb_graph.py near-pairs` (closest document pairs, each marked linked or not —
duplicate candidates and missing links across corpora) and the lint's
`divergent-mirror` (the same entry `id` at several paths with different
content); both in [link-graph.md](link-graph.md).

## Worktrees: one index, read-only from linked worktrees

The index belongs to the **main checkout** (the working tree that holds the
real `.git` directory). From a linked worktree, `kb_search.py` resolves the
same file through `git rev-parse --git-common-dir`, opens it **read-only**
and skips the lazy refresh: unmerged worktree content never enters the shared
index, and switching branches no longer re-embeds the same entries back and
forth. When the worktree's files differ from what the index holds, the search
prints a one-line warning naming how many files it is behind on; refresh from
the main checkout (or let its SessionStart hook do it). `kb_index.py` refuses
to write from a worktree. `CCMEMO_KB_INDEX=/abs/path/kb.db` overrides all of
this — an explicit path is yours to write — and remains the way to give a
worktree an index of its own. Pinning it by hand is no longer needed just to
search from a worktree.

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
# adjust CCMEMO_KB_ROOT / CCMEMO_KB_INDEXER env vars if your layout differs
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
