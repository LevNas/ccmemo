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
- See `/record-knowledge` skill for full details
