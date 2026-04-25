# Contributing to ccmemo

Thanks for taking an interest in ccmemo! This short guide explains the project's focus and how to contribute effectively.

## Project Focus

ccmemo is a **Claude Code plugin** that ships:

- The `/record-knowledge`, `/plan-task`, and `/review-knowledge` skills
- Supporting hooks, scripts, and the knowledge base template (Markdown files with YAML frontmatter under `.claude/knowledge/`)
- Documentation for the above

To keep the project simple and easy to maintain, it deliberately stays within these boundaries.

### Adapting ccmemo for Other Use Cases

Ideas like porting ccmemo to other AI coding assistants, IDE plugins, or note-taking apps, or replacing the flat-Markdown storage with a database or cloud sync, are interesting — but they go beyond what this repository aims to provide.

If you'd like to explore those directions, **forking ccmemo and adapting it freely is very much encouraged**. The MIT License gives you full permission to do so, and a credit link back to this project is appreciated (see [License](README.md#license)).

If you're not sure whether your idea fits the project's focus, feel free to open an issue with the `question` label first — we can discuss it before you invest time in a PR.

## Plugin Conventions

ccmemo follows the LevNas plugin conventions maintained in [claudecode-plugins/docs/development-guide.md](https://github.com/LevNas/claudecode-plugins/blob/main/docs/development-guide.md). Document placement and SKILL.md frontmatter rules are summarized here.

| Location | Purpose | Audience |
|----------|---------|----------|
| `README.md` | Plugin overview and usage | Users (humans) |
| `skills/<name>/SKILL.md` | Skill definition with required frontmatter (`name`/`description`/`license`/`allowed-tools`) | Claude Code |
| `skills/<name>/references/` | Runtime reference resources | Claude Code |
| `docs/` | Developer/operator internal docs | Contributors (humans) |
| `hooks/` | Hook implementations and `hooks.json` | Claude Code |
| `templates/` | Project-scoped starter files copied into user projects | Users (humans) |

Run the central linter from claudecode-plugins before sending a PR:

```bash
bash ~/src/github.com/LevNas/claudecode-plugins/scripts/lint-skills.sh ~/src/github.com/LevNas/ccmemo
```

## Pull Request Guidelines

To make review smooth for everyone:

1. **Open an issue first** for changes beyond typo fixes or small documentation tweaks. This avoids wasted work and helps align on direction early.
2. **Keep PRs focused** — one logical change per PR makes review much easier.
3. **Update documentation** (`README.md`, `SKILL.md`, etc.) when your change affects user-facing behavior.
4. **Match the existing style** by running the linter shown under [Plugin Conventions](#plugin-conventions) and matching the tone of the surrounding Markdown.
5. **By submitting a PR**, you agree that your contribution will be licensed under the same MIT License as the rest of the project.

## Attribution When Forking

ccmemo is MIT licensed, so you're free to fork, modify, and redistribute it. The MIT License requires that you keep the copyright notice and license text in copies or substantial portions of the software.

Beyond that, if you build something based on ccmemo, a credit line in your README is appreciated:

> Based on [ccmemo](https://github.com/LevNas/ccmemo) by LevNas.

This isn't a license condition — just a friendly request that helps users trace the lineage of ideas.

## Questions

For anything that isn't a bug report or feature proposal, the `question` issue template is the right place.
