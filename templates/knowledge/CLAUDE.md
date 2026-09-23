---
schema_version: 2   # ccmemo conventions this knowledge base commits to (see docs/upgrading.md)
---

# Knowledge Base

Aggregated tacit knowledge from Claude Code work sessions.
One entry per file with YAML frontmatter.

## Structure

```
.claude/knowledge/
├── CLAUDE.md          # This index
└── entries/           # One entry per file (YAML frontmatter)
    └── YYYY/
        └── MM/
            └── YYYYMMDD-HHMMSS-author-slug.md
```

- Entries are organized by year/month subdirectories
- `rg` and `fd` recurse into subdirectories automatically — search commands remain unchanged
- Legacy flat entries (directly under `entries/`) remain functional

## Search

### Keyword / tag search

Instant, no setup — use when you know the literal term or tag:

```bash
# Fuzzy search by filename
fd -e md . .claude/knowledge/entries/ | fzf

# Search by tag
rg '#pitfall' .claude/knowledge/entries/
rg '#docker.*#pitfall' .claude/knowledge/entries/

# List all titles
rg '^title:' .claude/knowledge/entries/

# Active entries only
rg '^status: active' .claude/knowledge/entries/
```

### Semantic search (by meaning)

When keywords might miss the entry — synonyms, or a Japanese query against English
identifiers — use the `/recall-knowledge` skill. It runs hybrid search (ripgrep +
local vector embeddings + the `see:`-link graph) and falls back to ripgrep-only when
the vector index or its optional dependencies are absent, so it never breaks. On-demand
only; the per-prompt auto-injection hook stays ripgrep (instant, no model load). Setup:
see the plugin's `docs/hybrid-search.md`.

## Tag Registry

Add new tags here. Reuse existing tags when possible.

`#pitfall`
`#environment-specific`

## Entry Lifecycle

### Status
| Status | Meaning |
|--------|---------|
| `draft` | Unverified fragment — do not use as basis for decisions |
| `active` | Verified, current knowledge (default if omitted) |
| `superseded` | Replaced by newer entry — follow `superseded_by` link |
| `deprecated` | Obsolete, no longer relevant |

Non-active entries carry a body-top warning banner so direct readers see the state first, e.g. `> **⚠ superseded (YYYY-MM-DD)** — current: [replacement title](YYYY/MM/slug.md)`.

### Type
| Type | Meaning |
|------|---------|
| `knowledge` | Standalone verified fact (default if omitted) |
| `overview` | Topic entry point — summary + detail links |
| `detail` | Focused entry referenced from an overview |
| `fragment` | Isolated observation, not yet promoted |
| `synthesis` | Cross-cutting insight from multiple entries |

### Confidence
- `low` — observed once, unverified
- `mid` — partially verified
- `high` — well-established fact

## Rules

- Entries are mutable — edit in place, rely on git for history
- Use `deprecated` only when knowledge is genuinely obsolete
- New entries use `YYYY/MM/YYYYMMDD-HHMMSS-author-slug.md` naming (year/month subdirectory + timestamp + author for collision avoidance)
- Do NOT add links to subdirectory CLAUDE.md files — use tag search to find entries
- Existing entries without timestamp prefix remain as-is (no rename)
- New tags **must** be added to the Tag Registry within the same operation
- `tags:` is a YAML list, one quoted `"#tag"` per line. The older single-line form
  `tags: "#tag1 #tag2"` is still read by every tool — never bulk-rewrite old entries
- `status:` omitted or blank means `active`; the index, the graph CLI and the prompt
  hook all apply that same default
- `description:` is required: the entry's *trigger condition* (when to open it — the
  symptom, question or decision), 80–320 chars, most typical situation first. Not a
  summary; the title already states the conclusion. Search results and the prompt
  hook show it so one entry can be chosen without opening several
- Every `- see:` / `ref:` / `amends:` / `extends:` line carries a label after the link
  (`— why to follow it`); `amends:` / `extends:` targets must link back (or be
  superseded by) the entry. `kb_graph.py lint` checks all of this, and the post-write
  hook runs it on every entry you save
- See `/record-knowledge` skill for full details
