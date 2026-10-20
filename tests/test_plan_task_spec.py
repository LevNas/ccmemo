#!/usr/bin/env python3
"""Self-tests for the plan-task spec rules (create-plan writes a spec).

Run: python3 tests/test_plan_task_spec.py   (exit 0 = all pass)

The rules are text the main session and the plan subagent read, so these tests
check that the text keeps them: the section order of plan-v1.md, the Out of
Scope rule, the subagent's rules, and the main session's settle step.
"""

import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SKILL = os.path.join(ROOT, "skills", "plan-task", "SKILL.md")
PROCEDURE = os.path.join(ROOT, "skills", "plan-task", "procedure.md")

failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


procedure = read(PROCEDURE)
skill = read(SKILL)

creating = procedure.split("## Creating a Plan", 1)[1].split("\n## ", 1)[0]
step4 = creating.split("\n4. ", 1)[1].split("\n5. ", 1)[0]

headings = ["## Approach", "## Decisions", "## Out of Scope", "## Verification",
            "## Completion criteria", "## Related Knowledge"]
positions = [step4.find("`" + h) for h in headings]
check("step 4 lists the six sections", all(p >= 0 for p in positions))
check("step 4 lists them in order", positions == sorted(positions))

for rule in (
    'at least one entry, and "none" is not an entry',
    "Never invent a decision to fill a section",
    "leave the plan unwritten and return what is missing",
    "No user stories",
    "Headings may use the repository's language",
):
    check("step 4 rule: " + rule, rule in step4)

check("creating a plan stops early when settled is incomplete",
      "lacks the agreed Verification or the Out of Scope items, create nothing" in creating)
check("When to Use points single-session work to plan mode",
      "Claude Code's plan mode is enough" in procedure)
check("revised plans keep the sections",
      "has the same sections as `plan-v1.md`" in procedure)

for rule in (
    "For `create-plan` only, settle with the user first",
    "ccharness:settle-decisions",
    "get their agreement before the plan is written",
    '"none" is not an answer',
    "### settled（create-plan: agreed Verification and Out of Scope）",
    "{settled}",
    "returns what is missing instead of writing the plan",
):
    check("SKILL.md: " + rule, rule in skill)

if failures:
    print(f"{len(failures)} failure(s)")
    sys.exit(1)
print("all passed")
