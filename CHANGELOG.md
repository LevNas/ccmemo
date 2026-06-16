# Changelog

All notable changes to this project will be documented in this file.

## [1.11.0] - 2026-06-16

### Added
- `/recall-knowledge` skill: on-demand **hybrid semantic search** over the knowledge base — lexical (ripgrep + mecab) fused with local vector embeddings and the `see:`-link graph (RRF), bridging synonyms and JA-query/EN-identifier gaps that keyword search misses
- `scripts/kb_index.py` — build/refresh a per-machine vector index (sha256 incremental, idempotent)
- `scripts/kb_search.py` — hybrid query (lexical + vector + RRF + `see:` expansion + frontmatter filters) with lazy re-embed of changed entries
- `hooks/post-merge.sample` — consumer-side incremental re-index after `git pull`
- `docs/hybrid-search.md` — setup, usage, and verification (incl. corporate TLS notes)

### Requirements (optional — the feature is opt-in)
- Semantic search needs `uv` (or Python ≥3.10) plus `fastembed` and `sqlite-vec`. The embedding model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (~220 MB) is downloaded **once** and runs fully local — no knowledge text leaves the machine.
- **Graceful fallback**: without the index or these deps, `/recall-knowledge` falls back to ripgrep-only — nothing breaks.
- The vector index (`.claude/knowledge/.index/kb.db`) is a per-machine derived cache and **must not be committed** (gitignore it; see `docs/hybrid-search.gitignore-snippet.txt`).

### Notes
- The per-prompt `userpromptsubmit_knowledge_search.sh` hook is **unchanged** (stays ripgrep — instant, no model load). Semantic search is on-demand only.
- First-time setup / verification covers dependency install, model download, index build, and corporate TLS inspection (`uv --system-certs` + `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`); see `docs/hybrid-search.md`.

## [1.10.2] - 2026-04-16

### Fixed
- Remove Sonnet model pinning from the record-knowledge subagent so it inherits the active session model instead of forcing a fixed model

## [1.10.1] - 2026-04-10

### Removed
- Temporary rollout trace line in `userpromptsubmit_knowledge_search.sh` that appended to `/tmp/ccmemo-knowledge-hook.log` (verification complete)

## [1.10.0] - 2026-04-10

### Added
- UserPromptSubmit hook `userpromptsubmit_knowledge_search.sh` that auto-searches `.claude/knowledge/entries/` with mecab + rg and injects the top 5 hits as `additionalContext` (#46)
- Registered the hook via `hooks/hooks.json` so it loads automatically through `${CLAUDE_PLUGIN_ROOT}`

### Requirements
- `mecab`, `mecab-ipadic-utf8`, `jq`, `rg` must be available on PATH; the hook no-ops silently if any are missing

## [1.9.0] - 2026-04-07

### Changed
- Delegate review-knowledge to Sonnet subagent to minimize main context consumption
- Extract review-knowledge procedure into separate procedure.md
- Change review-knowledge allowed-tools from `Read, Grep, Glob, Edit, Write` to `Read, Agent`

## [1.8.0] - 2026-04-05

### Added
- Delegate record-knowledge to Sonnet subagent to minimize main context consumption (#43, #44)
- Structured prompt template for subagent delegation
- Plan-task subagent delegation support
- Subagent Delegation section in README

## [1.7.0] - 2026-03-31

### Added
- TaskCreate/TaskUpdate sync with plan-task progress tracking (#10)
- `session_state.md` for fast session recovery

## [1.6.0] - 2026-03-28

### Added
- Entry lifecycle management (active/stale/archived) (#22, #23)
- Granularity control for knowledge entries (#27)
- Correction flow for updating existing entries (#28)
- Synthesis support for merging related entries

## [1.5.0] - 2026-03-28

### Added
- Large entry splitting with soft size limits (#8, #29)

## [1.4.0] - 2026-03-28

### Added
- Tag registry auto-maintenance (#26)
- Overview/detail hierarchy for knowledge entries (#25)

## [1.3.0] - 2026-03-28

### Added
- Reference integrity check (#32)
- Issue link recommendation for knowledge entries (#35)

## [1.2.0] - 2026-03-28

### Added
- `YYYY/MM/` directory structure for knowledge entries (#24)
- Unidirectional link detection and auto-fix for tags/links
- SystemMessage logging to PostToolUse hook

### Fixed
- Review-knowledge link completion (#3)
- PostToolUse hook logging (#2)

## [1.1.0] - 2026-03-16

### Added
- Context Guard: three-stage defense against knowledge loss during context compaction
- Context-*.md incremental capture and checkpoint lifecycle
- Issue sync for plan creation, revision, and completion
- Auto-update active tasks after recording knowledge
- Environment-specific recording guidance
- Review-knowledge skill for knowledge base maintenance

### Changed
- Moved task/issue sync responsibility to plan-task

## [1.0.0] - 2026-03-10

### Added
- Record-knowledge skill with automatic see-link discovery
- Plan-task skill with Git-tracked and issue-centric modes
- Tag similarity check to prevent duplicate tags
- Plugin marketplace support via plugin.json
- MIT License

[1.11.0]: https://github.com/LevNas/ccmemo/compare/v1.10.2...v1.11.0
[1.10.2]: https://github.com/LevNas/ccmemo/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/LevNas/ccmemo/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/LevNas/ccmemo/compare/v1.9.0...v1.10.0
[1.9.0]: https://github.com/LevNas/ccmemo/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/LevNas/ccmemo/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/LevNas/ccmemo/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/LevNas/ccmemo/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/LevNas/ccmemo/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/LevNas/ccmemo/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/LevNas/ccmemo/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/LevNas/ccmemo/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/LevNas/ccmemo/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/LevNas/ccmemo/releases/tag/v1.0.0
