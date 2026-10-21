#!/usr/bin/env python3
"""Self-tests for the record-knowledge decision form (Decision Entries).

Run: python3 tests/test_record_knowledge_decision.py   (exit 0 = all pass)

The rules are text the recording subagent reads, so these tests check that the
text keeps them: the three tests, the shape of a decision entry, the tag, the
pointer from When to Record, the starter tag registry, and that the skill's
description (loaded every session) did not grow.
"""

import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SKILL = os.path.join(ROOT, "skills", "record-knowledge", "SKILL.md")
PROCEDURE = os.path.join(ROOT, "skills", "record-knowledge", "procedure.md")
REGISTRY = os.path.join(ROOT, "skills", "record-knowledge", "assets", "knowledge-CLAUDE.md")

# Length of the description (whitespace collapsed) when the decision form was added.
DESCRIPTION_CEILING = 437

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
registry = read(REGISTRY)

types_at = procedure.find("### Entry Types")
decision_at = procedure.find("### Decision Entries")
check("procedure has a Decision Entries section", decision_at >= 0)
check("Decision Entries comes after Entry Types", 0 <= types_at < decision_at)

section = procedure[decision_at:].split("\n### ", 1)[0] if decision_at >= 0 else ""

for rule in (
    "only when it passes all three tests",
    "**Hard to reverse**",
    "**Surprising without context**",
    "**A real trade-off**",
    "If any test fails, write an ordinary entry",
    "The tests decide the form, not whether to record",
    "**Opens with one to three sentences** (required)",
    "**Has a rejected-alternatives section only when**",
    "the decision turns down a proposal or request",
    "is a deferral, not a decision",
    "**Carries the `#decision` tag.**",
    "Do not add a `type` value for it",
    "follow the Amendment Rules",
    "Do not reshape existing entries into this form",
    "commit `5beb35b`, MIT",
):
    check("Decision Entries: " + rule, rule in section)

when = procedure.split("## When to Record", 1)[1].split("\n## ", 1)[0]
check("When to Record points decisions to Decision Entries",
      "tag with `#decision`; see Decision Entries" in when)

tags = registry.split("## Tag Registry", 1)[1].split("\n## ", 1)[0]
check("starter tag registry lists #decision", "`#decision`" in tags)

front = skill.split("---", 2)[1]
description = " ".join(front.split("description:", 1)[1].split("\nlicense:", 1)[0].split())
check(f"SKILL.md description did not grow (<= {DESCRIPTION_CEILING} chars, now {len(description)})",
      len(description) <= DESCRIPTION_CEILING)
check("SKILL.md does not repeat the decision form", "Decision Entries" not in skill)

if failures:
    print(f"{len(failures)} failure(s)")
    sys.exit(1)
print("all passed")
