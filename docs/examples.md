# Real-World Examples

Concrete scenarios showing how ccmemo fits into everyday work.

## Personal: Capturing knowledge from debugging sessions

While debugging a CI pipeline failure, Claude Code discovers that a dependency upgrade
changed its config file format. The fix is simple, but without recording the context,
the next session has no idea why the config looks the way it does.

With `/record-knowledge`, Claude Code saves the discovery:

```markdown
---
title: webpack 6 requires updated config format
author: "@alice"
created: 2026-03-08
status: active
tags: "#webpack #migration #pitfall"
---

webpack 6 dropped support for `module.rules[].loader` shorthand.
Must use `module.rules[].use` array format instead.

The CI failure after the upgrade was caused by this breaking change.
- ref: https://webpack.js.org/migrate/6/
- see: [Node.js 22 upgrade notes](nodejs-22-upgrade.md)
```

The next session — or a teammate's session — finds this entry automatically and avoids
repeating the same investigation.

## Team: Syncing progress through an issue tracker

A team manages their project roadmap in GitHub Issues. Each member uses Claude Code in
their own session, but progress needs to stay visible to everyone.

With `/plan-task` in issue-centric mode, Claude Code checks assigned issues at session
start (`gh issue list --assignee=@me`) and posts progress comments when work is done.
The issue's checklist is updated directly — no manual copy-paste between sessions.

```markdown
## Progress Update (2026-03-08)

### Completed
- Migrated database schema to v3
- Added index on `users.email` column

### Next Steps
- Update API validation to match new schema constraints
```

Every team member sees the latest state in the issue tracker, regardless of who worked
on it or which Claude Code session produced the update.
