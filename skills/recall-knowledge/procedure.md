# Recall Knowledge — Procedure

Hybrid semantic search over the knowledge base. Runs from the **main agent** (it executes code;
subagents are sandboxed and cannot run `uv` / Python). On-demand only.

## Inputs
- `query` — the search text (required)
- Optional filters: `status`, `tag` (repeatable), `type`, `created-from`, `created-to`, `top` (N)

## Step 1. Resolve paths
- `KB_ROOT` = `{project_root}/.claude/knowledge/entries`
- `SEARCH`  = `{plugin_root}/scripts/kb_search.py`
- `INDEX`   = `{project_root}/.claude/knowledge/.index/kb.db`
- `BUILDER` = `{plugin_root}/scripts/kb_index.py` (referenced only when advising a build)
- `GRAPH`   = `{plugin_root}/scripts/kb_graph.py` (pure stdlib — works without `uv` or `INDEX`)

## Step 2. Decide hybrid vs fallback
Hybrid is available **iff** `uv` is on `PATH` **and** `INDEX` exists.
- Both present → Step 3a (hybrid).
- Otherwise → Step 3b (ripgrep fallback).

Either way, if the recall looks **multi-hop** (see Step 3c), pull graph structure
first and use search only to find the starting entry.

## Step 3a. Hybrid search (preferred)
Run from Bash (main agent):

```bash
uv run "{plugin_root}/scripts/kb_search.py" "{KB_ROOT}" "<query>" --top 8 \
  [--status active] [--tag '#sometag'] [--type knowledge] \
  [--created-from YYYY-MM-DD] [--created-to YYYY-MM-DD] [--json]
```

Notes:
- **No network at search time**: the query is embedded with the locally cached model.
- kb_search.py **lazily re-embeds changed entries** before searching, so results stay fresh
  without a manual rebuild.
- First-ever run after install (deps not yet cached) makes `uv` fetch fastembed/sqlite-vec.
  Under corporate TLS inspection, add `--system-certs` and export
  `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` (see `{plugin_root}/docs/hybrid-search.md`). Normally
  the index build already primed the cache, so plain `uv run` works.
- A `UserWarning` about "mean pooling" is benign — ignore it.

## Step 3b. Fallback — ripgrep only (no index / no deps)
- Tokenise the query with `mecab` if present (mirroring the existing
  `userpromptsubmit_knowledge_search.sh` approach), else split on whitespace; then
  `rg -l <term> "{KB_ROOT}"` for each term and union the hits. For ASCII identifiers, `rg`
  the literal token.
- Tell the user semantic recall is **disabled**, and how to enable it:
  ```bash
  uv run "{plugin_root}/scripts/kb_index.py" "{KB_ROOT}"
  ```
  (first run downloads ~220 MB model; under corporate TLS see `docs/hybrid-search.md`)

## Step 3c. Structure first when the recall is multi-hop
Signals that a flat top-N search will NOT answer in one shot:
- Tracing lineage: "how did this decision evolve", "what led to X"
- Connecting topics: "how are X and Y related", "is there prior art linking X to Y"
- Mapping an area: "everything we know around X", before refactoring a topic cluster

For those, do **not** loop search → Read → follow `see:` → Read again (each hop reads a
full body just to decide where to go next). Instead:

```bash
python3 "{plugin_root}/scripts/kb_graph.py" --root "{KB_ROOT}" neighborhood <entry> --depth 2
python3 "{plugin_root}/scripts/kb_graph.py" --root "{KB_ROOT}" path <entryA> <entryB>
python3 "{plugin_root}/scripts/kb_graph.py" --root "{KB_ROOT}" lineage <entry>
```

- Entries are addressed by unique filename substring; use Step 3a/3b (or `stats` for
  hubs) only to identify the starting entry when it is not already known.
- Decision-evolution questions map directly to `lineage`: it prints what the entry
  (transitively) replaced, the replacement chain forward, and the **current
  authority** — derived from the `superseded_by:` frontmatter, one command instead
  of hand-following links.
- Output is structure only — IDs, titles, edge kinds, never body text — so it is cheap
  to keep in context. Plan the route from it, then **Read only the endpoint entries**
  (typically 1–3) that actually answer the question.
- Runs with plain `python3` (stdlib only): available even when hybrid search is not.

## Step 4. Present results
- Show the ranked entries as printed by kb_search.py: score, title, relpath, tags, snippet.
- For the top 1–2 hits, **Read** the entry file when its content is needed to answer.
- Prefer `status: active`; if a top hit is `superseded`, run
  `python3 "{plugin_root}/scripts/kb_graph.py" --root "{KB_ROOT}" lineage <entry>` to
  jump to the current authority (the chain may be multi-step), answer from that entry,
  and note the supersession. For `deprecated` hits, note it and prefer `see:`-linked
  replacements.
- Keep output concise — return the ranked list and the synthesized answer, not raw tool noise.
