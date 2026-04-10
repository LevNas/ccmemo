# Changelog

All notable changes to this project will be documented in this file.

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
