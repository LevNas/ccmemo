# Plan & Task Persistence

Session-spanning plans and task tracking for Claude Code.

## Structure

```
.claude/tasks/
├── readme.md                  # Index of all plans
├── <slug>-<account>-<date>/
│   ├── readme.md              # Purpose and current state
│   ├── plan-v1.md             # Plan (immutable once created)
│   ├── todo.md                # Task progress (frequently updated)
│   └── ...
```

## Quick Rules

- Check `readme.md` at session start for incomplete plans
- Update `todo.md` only during work — never edit `plan-vN.md`
- Task markers: `- [ ]` pending, `- [~]` in progress, `- [x]` done
- To revise a plan, create `plan-vN+1.md` (keep previous versions)
- On session end, update `<slug>/readme.md` with handoff notes
- See `/plan-task` skill for full procedures
