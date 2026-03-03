# ccmemo

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill and knowledge base template for capturing tacit knowledge — quirks, pitfalls, decisions, and lessons learned — directly into your project repository.

## What It Does

- Provides a `/record-knowledge` skill that Claude Code uses to capture discoveries during work
- Stores knowledge as individual Markdown files with YAML frontmatter in `.claude/knowledge/entries/`
- Supports tagging, cross-referencing (synapse links), and full-text search
- Entries are mutable — edit in place, with git tracking change history

## Install

Copy the skill and knowledge base template into your project:

```bash
# From your project root
cp -r path/to/ccmemo/skills/record-knowledge .claude/skills/record-knowledge
cp -r path/to/ccmemo/templates/knowledge .claude/knowledge
```

Your project should now have:

```
your-project/
└── .claude/
    ├── skills/
    │   └── record-knowledge/
    │       └── SKILL.md
    └── knowledge/
        ├── CLAUDE.md
        └── entries/
            └── .gitkeep
```

## Usage

### Recording Knowledge

Claude Code automatically uses `/record-knowledge` when it discovers something worth noting. You can also trigger it explicitly:

```
/record-knowledge
```

Claude Code will create an entry like `.claude/knowledge/entries/20260302-143052-alice-docker-compose-port-conflict.md`:

```markdown
---
title: Docker Compose port conflict with host nginx
author: "@alice"
created: 2026-03-02
status: active
tags: "#docker #nginx #pitfall"
---

When running `docker compose up`, port 80 conflicts with the host's nginx.
Use `ports: ["8080:80"]` or stop host nginx first.

- ref: [Docker Compose networking](https://docs.docker.com/compose/networking/)
- see: [Nginx reverse proxy setup](nginx-reverse-proxy.md) — related configuration
```

### Searching Entries

```bash
# Fuzzy search by filename
fd -e md . .claude/knowledge/entries/ | fzf

# Search by tag
rg '#pitfall' .claude/knowledge/entries/

# List all titles
rg '^title:' .claude/knowledge/entries/

# Active entries only
rg '^status: active' .claude/knowledge/entries/
```

### Using Knowledge in CLAUDE.md

Add a lookup section to your project's `.claude/CLAUDE.md` so Claude Code checks relevant entries before starting work:

```markdown
## Knowledge Base Lookup

Before starting work, search for relevant active knowledge entries:

\```bash
rg '^status: active' .claude/knowledge/entries/ -l | xargs rg '<keyword>' -l
\```
```

## Customization

### Tags

Edit `.claude/knowledge/CLAUDE.md` to maintain a tag registry for your project. Tags use lowercase kebab-case with `#` prefix as a flat list:

```
`#docker` `#postgresql` `#react` `#pitfall` `#workaround` `#design-practice`
```

Claude Code checks the registry before creating new tags to avoid duplicates.

### Entry Author

The `author` field in entries defaults to `@<username>`. Set this to match your Git hosting platform username.

### Integration with Project Workflow

You can extend the skill by editing `SKILL.md` to fit your workflow — for example, adding project-specific tag categories or linking to your issue tracker.

## License

MIT
