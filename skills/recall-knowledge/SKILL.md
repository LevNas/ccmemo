---
name: recall-knowledge
description: >-
  Recall knowledge base entries by meaning, not just keywords — hybrid search (lexical ripgrep +
  local vector embeddings + see-link graph) over `.claude/knowledge/entries/`. Use when looking for
  prior knowledge, decisions, pitfalls, or context that may be worded differently from the query
  (e.g. a Japanese query vs English identifiers, or synonyms the entry does not literally contain).
  Falls back to ripgrep-only when the vector index or its dependencies are absent. On-demand only —
  it is NOT wired into the per-prompt hook (that stays ripgrep for instant, model-free injection).
license: MIT
allowed-tools: Bash, Read
---

# Recall Knowledge

## Goal
Surface the most relevant knowledge entries for a query by meaning — bridging synonyms and
cross-language wording (e.g. Japanese ↔ English identifiers) that literal keyword search misses.

## When to Use
- Searching the knowledge base for prior art, decisions, pitfalls, or related context
- The query may be worded differently than the entries (synonyms, JA query vs EN identifiers)
- Before starting work on a topic, to pull related accumulated knowledge
- NOT for per-prompt automatic injection — that stays ripgrep via the existing
  `userpromptsubmit_knowledge_search.sh` hook (instant, no model load)

## Execution (run directly — do NOT delegate to a subagent)

IMPORTANT: hybrid search executes code (`uv run` a Python script). Subagents run in a sandbox
that blocks code execution, networking, and out-of-cwd writes, so this skill runs from the
MAIN agent's Bash — do NOT spawn an Agent for the search itself.

1. Read the procedure file at: {plugin_root}/skills/recall-knowledge/procedure.md
2. Follow it: resolve paths, decide hybrid vs ripgrep-fallback, run the search, present the
   ranked results, and Read the top entries when their content is needed for the answer.

Paths:
- Knowledge base: {project_root}/.claude/knowledge/
- Search script:  {plugin_root}/scripts/kb_search.py
- Index builder:  {plugin_root}/scripts/kb_index.py (only to advise building the index)

IMPORTANT: The procedure / script paths use the plugin's base directory, NOT the project
directory. Read the "Base directory for this skill" line from the skill loading message to
determine `{plugin_root}`.
