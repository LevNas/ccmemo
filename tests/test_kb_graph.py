#!/usr/bin/env python3
"""Dependency-free self-tests for scripts/kb_graph.py.

Run: python3 tests/test_kb_graph.py   (exit 0 = all pass)

Builds a synthetic knowledge base in a temp dir shaped like a real project
(<repo>/.claude/knowledge/entries/YYYY/MM/...) so link resolution and the
broken-link vs out-of-tree distinction behave as in production.
"""

import json
import os
import subprocess
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import kb_graph  # noqa: E402

KB_GRAPH = os.path.join(SCRIPTS, "kb_graph.py")

ENTRY_A = "2026/07/20260701-100000-alice-topic-a.md"
ENTRY_B = "2026/07/20260701-110000-alice-topic-b.md"
ENTRY_C = "2026/07/20260702-090000-alice-orphan-c.md"
ENTRY_D = "2026/07/notes.md"


def make_kb(base):
    """Create <base>/repo/.claude/knowledge/{entries,CLAUDE.md}; return entries root."""
    root = os.path.join(base, "repo", ".claude", "knowledge", "entries")
    os.makedirs(os.path.join(root, "2026", "07"))

    def write(relpath, text):
        path = os.path.join(root, relpath)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    write(ENTRY_A, f"""---
title: Topic A
created: 2026-07-01
status: active
tags: "#pitfall"
---

Body A.

- see: [Topic B]({ENTRY_B}) — forward link
- see: [Topic B]({ENTRY_B}) — duplicate on purpose
- ref: [external](https://example.com/doc) — ignored
- ref: [in-repo missing](../../../rules/missing.md) — broken inside repo
- ref: [outside repo](../../../../../../outside.txt) — escapes the repo
""")
    write(ENTRY_B, f"""---
title: Topic B
created: 2026-07-01
status: active
tags: "#pitfall #mystery"
---

Body B.

- see: [Topic A]({ENTRY_A}) — back link
- see: [Topic B]({ENTRY_B}) — self link on purpose
""")
    write(ENTRY_C, """---
title: Orphan C
created: 2026-07-02
status: active
tags: "#docker"
---

No links at all.
""")
    write(ENTRY_D, "No frontmatter, bad filename.\n")

    registry = os.path.join(base, "repo", ".claude", "knowledge", "CLAUDE.md")
    with open(registry, "w", encoding="utf-8") as f:
        # Both registry line forms must be recognised.
        f.write("# Knowledge Base\n\n## Tag Registry\n\n"
                "- #pitfall — recurring traps (2)\n"
                "`#docker`\n")
    return root


def run_cli(root, *argv):
    return subprocess.run(
        [sys.executable, KB_GRAPH, "--root", root, *argv],
        capture_output=True, text=True, timeout=30,
    )


def test_load_graph_nodes_edges_and_problems():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        nodes, edges, problems = kb_graph.load_graph(root)
        assert set(nodes) == {ENTRY_A, ENTRY_B, ENTRY_C, ENTRY_D}, nodes.keys()
        # A→B and B→A survive; duplicate and self link are dropped as findings.
        assert {(s, d) for s, d, _k, _r in edges} == {(ENTRY_A, ENTRY_B), (ENTRY_B, ENTRY_A)}, edges
        checks = {(nid, check) for nid, check, _ in problems}
        assert (ENTRY_A, "duplicate-link") in checks, checks
        assert (ENTRY_A, "broken-link") in checks, checks
        assert (ENTRY_A, "out-of-tree") in checks, checks
        assert (ENTRY_B, "self-link") in checks, checks
        assert (ENTRY_D, "missing-title") in checks, checks
        assert (ENTRY_D, "filename") in checks, checks
        # https ref must produce neither an edge nor a finding.
        assert not any("example.com" in d for _n, _c, d in problems), problems


def test_components_and_orphans():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        nodes, edges, _problems = kb_graph.load_graph(root)
        comps = kb_graph.components(nodes, edges)
        assert [len(c) for c in comps] == [2, 1, 1], [len(c) for c in comps]
        assert set(comps[0]) == {ENTRY_A, ENTRY_B}, comps[0]


def test_resolve_entry():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        nodes, _edges, _problems = kb_graph.load_graph(root)
        assert kb_graph.resolve_entry(nodes, "topic-a") == ENTRY_A
        assert kb_graph.resolve_entry(nodes, ENTRY_B) == ENTRY_B
        for bad in ("topic", "no-such-entry"):  # ambiguous / no match
            try:
                kb_graph.resolve_entry(nodes, bad)
                raise AssertionError(f"resolve_entry('{bad}') should exit")
            except SystemExit as e:
                assert "error" in str(e.code), e.code


def test_cli_stats_json():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        res = run_cli(root, "--json", "stats")
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["nodes"] == 4 and data["edges"] == 2, data
        assert data["orphans"] == [ENTRY_C, ENTRY_D], data["orphans"]
        assert data["components"] == [2, 1, 1], data["components"]


def test_cli_neighborhood_and_path_json():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        res = run_cli(root, "--json", "neighborhood", "topic-a", "--depth", "1")
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert [n["id"] for n in data["neighbors"]] == [ENTRY_B], data
        # Structure only — no body text may leak into the output.
        assert "Body" not in res.stdout, res.stdout

        res = run_cli(root, "--json", "path", "topic-a", "topic-b")
        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout)["hops"] == 1, res.stdout

        res = run_cli(root, "path", "topic-a", "orphan-c")
        assert res.returncode == 1, "disconnected pair must exit 1"


def test_cli_lint_findings_and_scoping():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        res = run_cli(root, "--json", "lint")
        assert res.returncode == 1, "findings must exit 1"
        findings = json.loads(res.stdout)
        by_check = {}
        for f in findings:
            by_check.setdefault(f["check"], []).append(f["id"])
        # #pitfall (list form) and #docker (backtick form) are registered;
        # only #mystery is unknown.
        assert by_check.get("unknown-tag") == [ENTRY_B], by_check
        assert "#mystery" in [f["detail"] for f in findings if f["check"] == "unknown-tag"][0]
        # Scoping to one file keeps only its findings (pre-commit usage).
        res = run_cli(root, "--json", "lint", os.path.join(root, ENTRY_D))
        scoped = json.loads(res.stdout)
        assert {f["id"] for f in scoped} == {ENTRY_D}, scoped


def test_cli_lint_clean_exits_zero():
    with tempfile.TemporaryDirectory() as base:
        root = os.path.join(base, "repo", ".claude", "knowledge", "entries")
        os.makedirs(root)
        with open(os.path.join(root, "20260701-100000-alice-clean.md"), "w",
                  encoding="utf-8") as f:
            f.write('---\ntitle: Clean\ntags: "#pitfall"\n---\n\nNothing wrong here.\n')
        with open(os.path.join(base, "repo", ".claude", "knowledge", "CLAUDE.md"), "w",
                  encoding="utf-8") as f:
            f.write("- #pitfall — recurring traps (1)\n")
        res = run_cli(root, "lint")
        assert res.returncode == 0, (res.stdout, res.stderr)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
