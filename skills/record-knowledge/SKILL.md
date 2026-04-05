---
name: record-knowledge
description: >-
  Record tacit knowledge — quirks, pitfalls, dependencies, decisions, root causes — as tagged
  Markdown entries in `.claude/knowledge/entries/`. Use this skill whenever discoveries are made
  during work, when the user shares undocumented system behavior, or at plan completion to capture
  lessons learned. Also use when Claude Code makes a mistake pointed out by the user — record what
  happened, why it was wrong, and what to do next time.
license: MIT
allowed-tools: Read, Agent
---

# Record Knowledge

## Goal
Capture tacit knowledge discovered during work and make it available for future sessions.

## When to Reference
- **New session start**: Search `.claude/knowledge/entries/` for active entries related to the current task before starting work
  - If an `overview` entry exists for the topic, read it first — load `detail` entries on demand to save context
- **Progress update**: Check if related entries need updating based on new discoveries
- Not needed when resuming a session (context is already preserved)

## Execution

Delegate the recording work to a Sonnet subagent to minimize main context consumption.

1. Identify what knowledge to record from the user input or work context
2. Determine the knowledge base path (default: `.claude/knowledge/entries/`)
3. Spawn a subagent with the following configuration:

```
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  description: "record knowledge entry",
  prompt: |
    You are a knowledge recording agent. Read the procedure file and follow it precisely.

    ## Task
    Record the following knowledge:
    {knowledge_description}

    ## Arguments
    {arguments_from_user}

    ## Instructions
    1. Read the procedure file at: {plugin_root}/skills/record-knowledge/procedure.md
    2. Follow the procedure step by step
    3. The knowledge base is at: {project_root}/.claude/knowledge/
    4. Return: filename, # heading, ## headings, and linked entries
)
```

Replace `{knowledge_description}` with a clear summary of what to record, `{arguments_from_user}` with the ARGUMENTS passed to the skill, and `{plugin_root}` with the plugin's installation path (shown in the skill loading message as "Base directory for this skill"), and `{project_root}` with the project working directory.

4. Report the subagent's result to the user (filename, headings, linked entries only)

IMPORTANT: The procedure file path uses the plugin's base directory, NOT the project directory. Read the "Base directory for this skill" line from the skill loading message to determine the correct path.
