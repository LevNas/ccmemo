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

## Step 2. Decide hybrid vs fallback
Hybrid is available **iff** `uv` is on `PATH` **and** `INDEX` exists.
- Both present → Step 3a (hybrid).
- Otherwise → Step 3b (ripgrep fallback).

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

## Step 4. Present results
- Show the ranked entries as printed by kb_search.py: score, title, relpath, tags, snippet.
- For the top 1–2 hits, **Read** the entry file when its content is needed to answer.
- Prefer `status: active`; if a top hit is `superseded`/`deprecated`, note it and prefer its
  replacement (follow `superseded_by` / `see:` links).
- Keep output concise — return the ranked list and the synthesized answer, not raw tool noise.
