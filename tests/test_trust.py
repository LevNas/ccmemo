#!/usr/bin/env python3
"""Self-tests for hooks/lib/trust.py (schema 3: actors, tiers, frontmatter edits).

Run: python3 tests/test_trust.py   (exit 0 = all pass). Pure stdlib.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib import frontmatter  # noqa: E402
from lib import trust  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


def test_actors():
    for good in ("human:alice", "human:a.b-c_d", "claude-code", "claude-code/claude-fable-5-1",
                 "process:ccreview-gate"):
        check(f"actor ok {good}", trust.valid_actor(good))
    for bad in ("", "alice", "@alice", "human:", "human:@alice", "claude-code/", "process:",
                "bot:x", "human:alice bob", None, 3):
        check(f"actor rejected {bad!r}", not trust.valid_actor(bad))
    check("kind human", trust.actor_kind("human:alice") == "human")
    check("kind machine (agent)", trust.actor_kind("claude-code/m") == "machine")
    check("kind machine (process)", trust.actor_kind("process:gate") == "machine")
    check("kind invalid", trust.actor_kind("nope") == "")
    check("human_actor from author", trust.human_actor('"@alice"') == "human:alice")
    check("human_actor empty", trust.human_actor("") == "")


def test_datetimes():
    a = trust.parse_dt("2026-09-23T10:00:00+09:00")
    b = trust.parse_dt("2026-09-23T01:00:00Z")
    check("offsets compare", a == b)
    check("date only", trust.parse_dt("2026-09-23").date() == datetime.date(2026, 9, 23))
    check("naive read as UTC", trust.parse_dt("2026-09-23T01:00:00") == b)
    check("garbage is None", trust.parse_dt("yesterday") is None and trust.parse_dt(5) is None)


def test_tiers():
    meta = {"generated": {"by": "claude-code", "at": "2026-09-01T10:00:00+09:00"}}
    check("unverified", trust.verified_tier(meta) == ("", ""))
    meta["verified"] = [{"by": "human:alice", "at": "2026-09-02T10:00:00+09:00"}]
    check("human tier", trust.verified_tier(meta) == ("human", "2026-09-02T10:00:00+09:00"))
    meta["verified"].append({"by": "process:gate", "at": "2026-09-03T10:00:00+09:00"})
    check("latest verifier wins", trust.verified_tier(meta)[0] == "machine")
    meta["verified"].append({"by": "human:bob", "at": "2026-08-01T10:00:00+09:00"})
    check("expired event ignored for the tier", trust.verified_tier(meta)[0] == "machine")
    check("not expired while a newer event exists", not trust.verification_expired(meta))
    only_old = {"generated": meta["generated"],
                "verified": [{"by": "human:bob", "at": "2026-08-01T10:00:00+09:00"}]}
    check("expired when every event predates generated", trust.verification_expired(only_old))
    check("expired → unverified", trust.verified_tier(only_old) == ("", ""))
    check("invalid actor ignored",
          trust.verified_tier({"verified": [{"by": "bob", "at": "2026-09-02"}]}) == ("", ""))
    check("single mapping tolerated",
          trust.verified_tier({"verified": {"by": "human:x", "at": "2026-09-02"}})[0] == "human")
    check("no generated: nothing expires", not trust.verification_expired({"verified": []}))
    check("stale passed", trust.stale_after_passed({"stale_after": "2020-01-01"}))
    check("stale not yet", not trust.stale_after_passed({"stale_after": "2999-01-01"}))
    check("stale absent", not trust.stale_after_passed({}))
    check("tier order", trust.tier_at_least("human", "machine") and trust.tier_at_least("machine", "machine")
          and not trust.tier_at_least("", "machine") and trust.tier_at_least("", ""))


TEXT = ('---\ntitle: T\nauthor: "@alice"\ncreated: 2026-09-01\ntags:\n  - "#a"\n'
        'description: "d"\n---\n\nBody.\n\n- see: [x](y.md) — z\n')


def test_frontmatter_edits():
    check("bounds", trust.fm_bounds(TEXT) is not None)
    check("no frontmatter", trust.fm_bounds("Body only\n") is None)
    t = trust.set_key(TEXT, "id", ["id: 1234"], after="title")
    lines = t.splitlines()
    check("id inserted after title", lines[1] == "title: T" and lines[2] == "id: 1234", lines[:3])
    check("body untouched", t.endswith("Body.\n\n- see: [x](y.md) — z\n"))
    t2 = trust.set_key(t, "generated", ["generated:", "  by: claude-code", "  at: 2026-09-01T00:00:00+09:00"],
                       after="created")
    meta, _ = frontmatter.parse(t2)
    check("generated parses back", trust.generated_of(meta) == {"by": "claude-code", "at": "2026-09-01T00:00:00+09:00"})
    check("tags list survived", meta["tags"] == ["#a"], meta["tags"])
    t3 = trust.set_key(t2, "tags", ['tags: "#b"'])
    m3, _ = frontmatter.parse(t3)
    check("nested block replaced as a unit", m3["tags"] == ["#b"] and "generated:" in t3, t3)
    check("has_key", trust.has_key(t2, "generated") and not trust.has_key(t2, "verified"))
    t4 = trust.append_list_item(t2, "verified", trust.event_lines("human:alice", "2026-09-02", item=True),
                                after="generated")
    m4, _ = frontmatter.parse(t4)
    check("verified created after generated",
          trust.verified_events(m4) == [{"by": "human:alice", "at": "2026-09-02"}], t4)
    idx_gen = t4.index("generated:")
    idx_ver = t4.index("verified:")
    check("placement", idx_gen < idx_ver < t4.index("status") if "status" in t4 else idx_gen < idx_ver)
    t5 = trust.append_list_item(t4, "verified", trust.event_lines("process:gate", "2026-09-03", item=True))
    m5, _ = frontmatter.parse(t5)
    check("second event appended", [e["by"] for e in trust.verified_events(m5)] == ["human:alice", "process:gate"])
    t6 = trust.set_key(t5, "verified", ["verified: []"])
    t7 = trust.append_list_item(t6, "verified", trust.event_lines("human:bob", "2026-09-04", item=True))
    m7, _ = frontmatter.parse(t7)
    check("empty flow list rewritten as block", trust.verified_events(m7) == [{"by": "human:bob", "at": "2026-09-04"}], t7)
    check("append at end when no anchor",
          trust.set_key(TEXT, "stale_after", ["stale_after: 2027-01-01"]).splitlines()[7] == "stale_after: 2027-01-01")
    try:
        trust.set_key("no fm\n", "id", ["id: x"])
        check("set_key refuses without frontmatter", False)
    except ValueError:
        check("set_key refuses without frontmatter", True)


if __name__ == "__main__":
    test_actors()
    test_datetimes()
    test_tiers()
    test_frontmatter_edits()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall passed")
