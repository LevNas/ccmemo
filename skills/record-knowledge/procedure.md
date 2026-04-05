# Record Knowledge — Procedure

This file contains the detailed procedure for the record-knowledge subagent.

## Goal
Capture tacit knowledge discovered during work and make it available for future sessions.

## When to Record
- Undocumented behavior, quirks, or pitfalls
- Hardware/service characteristics shared by the user
- Dependencies or coupled settings discovered during configuration
- Decision rationale (why a particular approach was chosen)
- Root causes and fixes found during troubleshooting
- **Claude Code's own mistakes and prevention measures** — errors pointed out by the user, incorrect output, tool misuse, etc. Record specifically: what happened, why it was wrong, and what to do next time. Tag with `#pitfall`
- **Environment-specific behavior** — when a discovery is tied to a specific PC, OS, network, or toolchain version, include the environment details in the entry body. Tag with `#environment-specific`

## Setup

If `.claude/knowledge/CLAUDE.md` does not exist, copy from assets:

```bash
mkdir -p .claude/knowledge/entries
cp assets/knowledge-CLAUDE.md .claude/knowledge/CLAUDE.md
```

## Recording Flow

1. Create `.claude/knowledge/entries/YYYY/MM/YYYYMMDD-HHMMSS-author-slug.md` with YAML frontmatter
2. For new discoveries without enough detail yet, write a temporary note in the working directory and convert to an entry later
3. Do NOT add links to subdirectory `CLAUDE.md` files — use tag search to find entries instead
4. Act autonomously — create and edit entries without asking for user confirmation

## Entry Location
- `.claude/knowledge/entries/YYYY/MM/YYYYMMDD-HHMMSS-author-slug.md` — one file per entry, organized by year/month
- Timestamp prefix ensures chronological ordering and collision avoidance
- Author field uses your Git hosting platform account name (without `@`)
- Slug is descriptive kebab-case
- Example: `2026/03/20260302-143052-alice-docker-compose-port-conflict.md`
- Create `YYYY/MM/` subdirectory if it doesn't exist

## Entry Format (YAML Frontmatter)
```markdown
---
title: <title>
author: "@<username>"
created: YYYY-MM-DD
status: draft | active | superseded | deprecated
type: knowledge | overview | detail | fragment | synthesis
confidence: low | mid | high
superseded_by: YYYY/MM/newer-entry-slug.md   # only when status: superseded
tags: "#tag1 #tag2 ..."
---

<body — concrete facts, procedures, code examples, etc.>

- ref: [display text](URL or relative path)
- see: [related entry title](YYYY/MM/slug.md) — relationship description
```

- Keep entries focused and under **100 KB** where possible
- When creating an entry that exceeds **300 lines**, consider splitting it
- `type` is optional — defaults to `knowledge` if omitted

### Entry Types

| Type | Meaning | When to Use |
|------|---------|-------------|
| `knowledge` | Standalone verified fact (default) | Most entries — independent pieces of knowledge |
| `overview` | Topic entry point with summary and detail links | When 3+ detail entries exist for a topic |
| `detail` | Focused entry referenced from an overview | Deep-dive into a specific aspect of a topic |
| `fragment` | Isolated observation, not yet promoted | Quick notes that may become knowledge later |
| `synthesis` | Cross-cutting insight from multiple entries | Integrating patterns across entries |

### Tag Guidelines
- Assign tags autonomously for optimal searchability
- Naming: lowercase kebab-case with `#` prefix (e.g., `#docker`, `#typescript`, `#pitfall`)
- Check the tag registry in `.claude/knowledge/CLAUDE.md` before creating new tags to avoid duplicates

#### Similarity Check (on every entry creation)
Before assigning tags, scan the tag registry for near-duplicates:
- **Singular/plural**: `#backup` vs `#backups` → use the existing form
- **Abbreviation/full**: `#k8s` vs `#kubernetes` → use the existing form
- **Synonym**: `#error` vs `#bug` → use the existing form
- **Substring overlap**: `#windows-service` vs `#win-service` → use the existing form
If a near-duplicate is found, reuse the existing tag. Do not create a new one.

### ref / see Link Format
- External: `- ref: [title](https://example.com/...)`
- In-repo: `- ref: [path](../../../relative-path)` (relative from `.claude/knowledge/entries/`)

### see Links (Synapse Formation Between Entries)
- Add `see:` links to related entries when creating or editing an entry
- Within `entries/`, use entries/-relative paths: `- see: [title](YYYY/MM/slug.md) — relationship`
- Describe the relationship briefly after `—`
- Relevance criteria:
  - **Sequential steps**: procedure step dependencies, workflow stages
  - **Same technology, different pitfalls**: multiple gotchas for one tool
  - **Prerequisite → application**: setup steps → usage caveats
  - **Design decision ↔ rationale**: architecture choice ↔ supporting evidence
- Bidirectional links by default (if A → B, add B → A too)

## Status Definitions

| Status | Meaning |
|--------|---------|
| `draft` | Unverified fragment — reference with caution |
| `active` | Verified, current knowledge |
| `superseded` | Replaced by a newer entry — follow `superseded_by` link |
| `deprecated` | Obsolete, no longer relevant |

### Confidence Levels

| Level | Meaning |
|-------|---------|
| `low` | Observed once, not yet reproduced |
| `mid` | Reproduced or confirmed in some contexts |
| `high` | Verified multiple times, well-established |

### Correction Flow (superseded)
1. Set `status: superseded` and add `superseded_by: YYYY/MM/newer-entry-slug.md`
2. Create the replacement entry with a `- see:` link: `corrects [original title](YYYY/MM/original.md)`
3. Keep the original entry intact

## Amendment Rules
- Entries are **mutable** — edit in place (git tracks change history)
- Use `deprecated` only when knowledge is genuinely obsolete
- Use `superseded` when an entry is replaced by a corrected version

## Entry Granularity
**1 entry = 1 topic.** Splitting guidelines:
- 1 pitfall → 1 entry
- 1 design decision + rationale → 1 entry
- 1 root cause + fix → 1 entry

## Procedure

1. Extract knowledge from user input or work discoveries
2. Read the tag registry in `.claude/knowledge/CLAUDE.md`
3. Select tags — reuse existing tags; check for near-duplicates before creating any new tag
4. **Find related entries** (see link candidates) — run before writing so links are included from the start:
   a. **Tag search**: Grep `entries/` for each tag assigned in step 3
   b. **Keyword search**: Grep for 2–3 distinctive terms from the title or body
   c. **Narrow results**: Skip `deprecated`/`superseded` entries. Read titles and tags to judge relevance
   d. **AI-assisted suggestion**: From the candidate list, select entries whose tags or title overlap meaningfully with the new entry. Prefer entries that share 2+ tags or describe the same technology/workflow. Automatically add `see:` links for all selected entries
   e. **Prepare links**: For each related entry, draft a `- see:` line with a brief relationship description
5. Create `.claude/knowledge/entries/YYYY/MM/YYYYMMDD-HHMMSS-author-slug.md` (or edit existing entry) — include the see links drafted in step 4
6. **Tag registry update (mandatory)**: If a new tag was created, add it to the tag registry in `.claude/knowledge/CLAUDE.md` within the same operation
7. **Add backlinks**: For each entry linked in step 4, edit that entry to add a reciprocal `- see:` link pointing back to the new entry
8. Return a summary of what was recorded: filename, `#` heading, `##` headings, and linked entries
