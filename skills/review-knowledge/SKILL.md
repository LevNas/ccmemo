---
name: review-knowledge
description: >-
  Review and maintain the knowledge base — find stale entries, orphan entries without connections,
  missing links between related entries, and generate topic summaries. Supports the "internalization"
  phase of knowledge management by surfacing knowledge for periodic review and reflection.
license: MIT
allowed-tools: Read, Agent
---

# Review Knowledge

## Goal
Maintain knowledge base health and surface entries for review, helping the user internalize accumulated knowledge through periodic reflection.

## When to Use
- User explicitly requests a knowledge review (e.g., "review knowledge", "check knowledge base")
- At the end of a significant work phase or project milestone
- When the user wants to understand the current state of knowledge on a topic
- Periodically (e.g., weekly/monthly) to keep the knowledge base healthy

## Execution

Delegate the review work to a Sonnet subagent to minimize main context consumption.

1. Determine the review mode from user input:
   - No argument or "health" → `health`
   - `topic:<keyword>` → `topic` with the specified keyword
   - `fix` → `fix`
2. Determine the knowledge base path (default: `.claude/knowledge/entries/`)
3. Spawn a subagent with the following configuration:

```
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  description: "review knowledge base",
  prompt: |
    You are a knowledge base review agent. Read the procedure file and follow it precisely.

    ## Input
    ### mode
    {mode}

    ### topic_keyword (only for topic mode)
    {topic_keyword}

    ### current_date
    {current_date}

    ## Instructions
    1. Read the procedure file at: {plugin_root}/skills/review-knowledge/procedure.md
    2. Follow the procedure step by step for the specified mode
    3. The knowledge base is at: {project_root}/.claude/knowledge/
    4. Return the full review report in the output format specified in the procedure
)
```

Replace the placeholders:
- `{mode}` — review mode: `health`, `topic`, or `fix`
- `{topic_keyword}` — keyword or tag for topic mode (empty for other modes)
- `{current_date}` — today's date in YYYY-MM-DD format (needed for stale entry detection)
- `{plugin_root}` — the plugin's installation path (shown in the skill loading message as "Base directory for this skill")
- `{project_root}` — the project working directory

4. Report the subagent's result to the user

IMPORTANT: The procedure file path uses the plugin's base directory, NOT the project directory. Read the "Base directory for this skill" line from the skill loading message to determine the correct path.
