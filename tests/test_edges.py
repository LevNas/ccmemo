#!/usr/bin/env python3
"""Self-tests for hooks/lib/edges.py — typed link edges with labels.

Run: python3 tests/test_edges.py   (exit 0 = all pass)
Pure stdlib.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib import edges  # noqa: E402

BODY = """# Title

## 関連

- see: [Target A](2026/09/20260901-000000-u-a.md) — なぜ辿るか（ラベル）
- ref: [Target B](2026/09/20260902-000000-u-b.md#section)
- amends: [Old C](2026/08/20260801-000000-u-c.md): colon-separated label
- extends: [D](2026/08/20260802-000000-u-d.md) - hyphen label
- see: [External](https://example.com/x) — must be skipped
- see: [Dup A](2026/09/20260901-000000-u-a.md) — second occurrence, same kind
- ref: [A again](2026/09/20260901-000000-u-a.md) — same target, other kind
- note: [Not a link kind](x.md)
- see: [Broken [bracket] label](2026/09/20260903-000000-u-e.md) — loose only
"""

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


def test_body_links():
    got = edges.extract_edges({}, BODY)
    targets = [(e["rel"], e["target"]) for e in got]
    check("kinds and order", targets == [
        ("see", "2026/09/20260901-000000-u-a.md"),
        ("ref", "2026/09/20260902-000000-u-b.md"),
        ("amends", "2026/08/20260801-000000-u-c.md"),
        ("extends", "2026/08/20260802-000000-u-d.md"),
        ("ref", "2026/09/20260901-000000-u-a.md"),
    ], repr(targets))
    labels = {(e["rel"], e["target"]): e["label"] for e in got}
    check("em-dash label", labels[("see", "2026/09/20260901-000000-u-a.md")] == "なぜ辿るか（ラベル）")
    check("no label -> empty", labels[("ref", "2026/09/20260902-000000-u-b.md")] == "")
    check("colon separator stripped", labels[("amends", "2026/08/20260801-000000-u-c.md")] == "colon-separated label")
    check("hyphen separator stripped", labels[("extends", "2026/08/20260802-000000-u-d.md")] == "hyphen label")
    check("anchor removed", all("#" not in e["target"] for e in got))
    check("external skipped", not any("example.com" in e["target"] for e in got))
    check("duplicate (target, rel) kept once", targets.count(("see", "2026/09/20260901-000000-u-a.md")) == 1)


def test_related_docs():
    fm = {"related_docs": [
        {"path": "design/a.md", "label": "spec this implements"},
        {"path": "design/b.md", "rel": "extends", "label": "detail"},
        {"path": "design/c.md", "rel": "bogus"},
        "design/d.md",
        {"path": "https://example.com/e"},
    ]}
    got = edges.extract_edges(fm, "")
    check("related_docs count", len(got) == 4, repr(got))
    check("related_docs default rel", got[0] == {"target": "design/a.md", "rel": "see", "label": "spec this implements"})
    check("related_docs explicit rel", got[1]["rel"] == "extends")
    check("related_docs unknown rel -> see", got[2]["rel"] == "see")
    check("related_docs bare string", got[3] == {"target": "design/d.md", "rel": "see", "label": ""})


def test_merge_and_registry():
    fm = {"related_docs": [{"path": "2026/09/20260901-000000-u-a.md", "label": "fm copy"}]}
    got = edges.extract_edges(fm, "- see: [A](2026/09/20260901-000000-u-a.md) — body copy\n")
    check("body wins over related_docs on same (target, rel)", len(got) == 1 and got[0]["label"] == "body copy")
    only = edges.extract_edges(fm, "- see: [A](2026/09/20260901-000000-u-a.md) — body copy\n", extractors=["related-docs"])
    check("extractor selection", only == [{"target": "2026/09/20260901-000000-u-a.md", "rel": "see", "label": "fm copy"}])
    try:
        edges.extract_edges({}, "", extractors=["nope"])
        check("unknown extractor raises", False)
    except KeyError:
        check("unknown extractor raises", True)


def test_regex_compat():
    # kb_graph consumes LINK_RE via findall as (kind, text, target): keep 3 groups.
    m = edges.LINK_RE.findall(BODY)
    check("LINK_RE three groups", m and len(m[0]) == 3 and m[0] == ("see", "Target A", "2026/09/20260901-000000-u-a.md"))
    loose = [ln for ln in BODY.splitlines() if edges.LOOSE_LINK_RE.match(ln) and not edges.LINK_RE.match(ln)]
    check("loose-only line detected", loose == ["- see: [Broken [bracket] label](2026/09/20260903-000000-u-e.md) — loose only"], repr(loose))


if __name__ == "__main__":
    test_body_links()
    test_related_docs()
    test_merge_and_registry()
    test_regex_compat()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall passed")
