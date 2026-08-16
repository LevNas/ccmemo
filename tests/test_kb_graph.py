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


ENTRY_E = "2026/07/20260703-120000-alice-section-only-e.md"


def add_entry_e(root):
    """Entry with a 関連 section but no links yet (heading anchor case)."""
    path = os.path.join(root, ENTRY_E)
    with open(path, "w", encoding="utf-8") as f:
        f.write("""---
title: Section Only E
created: 2026-07-03
status: active
tags: "#pitfall"
---

Body E.

## 関連

""")
    return path


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def test_link_add_appends_after_last_link_and_is_idempotent():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        res = run_cli(root, "link-add", "topic-b", "orphan-c",
                      "--reason", "test relation")
        assert res.returncode == 0, (res.stdout, res.stderr)
        text = read(root, ENTRY_B)
        line = f"- see: [Orphan C]({ENTRY_C}) — test relation"
        assert line in text, text
        # appended directly after the previous last link line
        assert text.index(line) > text.index("self link on purpose"), text
        # idempotent rerun: no duplicate, exit 0
        res = run_cli(root, "link-add", "topic-b", "orphan-c",
                      "--reason", "test relation")
        assert res.returncode == 0 and "already linked" in res.stdout, res.stdout
        assert read(root, ENTRY_B).count(f"({ENTRY_C})") == 1


def test_link_add_uses_section_heading_anchor():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        add_entry_e(root)
        res = run_cli(root, "link-add", "section-only-e", "topic-a",
                      "--reason", "via heading")
        assert res.returncode == 0, (res.stdout, res.stderr)
        text = read(root, ENTRY_E)
        assert f"## 関連\n\n- see: [Topic A]({ENTRY_A}) — via heading" in text, text


def test_link_add_fails_loudly_without_anchor():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        before = read(root, ENTRY_C)
        res = run_cli(root, "link-add", "orphan-c", "topic-a", "--reason", "x")
        assert res.returncode != 0, "no anchor must fail"
        assert "manually" in res.stderr, res.stderr
        assert read(root, ENTRY_C) == before, "file must be untouched"


def test_link_add_bidirectional_validates_before_writing():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        before_a = read(root, ENTRY_A)
        # reverse direction (orphan-c) has no anchor -> nothing may be written
        res = run_cli(root, "link-add", "topic-a", "orphan-c",
                      "--reason", "x", "--bidirectional")
        assert res.returncode != 0, "must fail on the reverse leg"
        assert read(root, ENTRY_A) == before_a, "no partial application"
        # both legs valid -> both written
        add_entry_e(root)
        res = run_cli(root, "link-add", "topic-a", "section-only-e",
                      "--reason", "fwd", "--reverse-reason", "back",
                      "--bidirectional")
        assert res.returncode == 0, (res.stdout, res.stderr)
        assert f"({ENTRY_E}) — fwd" in read(root, ENTRY_A)
        assert f"({ENTRY_A}) — back" in read(root, ENTRY_E)


def test_link_add_dry_run_and_bad_targets():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        before = read(root, ENTRY_B)
        res = run_cli(root, "link-add", "topic-b", "orphan-c",
                      "--reason", "x", "--dry-run")
        assert res.returncode == 0 and "dry-run" in res.stdout, res.stdout
        assert read(root, ENTRY_B) == before, "dry-run must not write"
        # target without a frontmatter title
        res = run_cli(root, "link-add", "topic-b", "notes", "--reason", "x")
        assert res.returncode != 0 and "title" in res.stderr, res.stderr
        # self link
        res = run_cli(root, "link-add", "topic-b", "topic-b", "--reason", "x")
        assert res.returncode != 0 and "self-link" in res.stderr, res.stderr


def test_link_add_result_passes_lint():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        add_entry_e(root)
        res = run_cli(root, "link-add", "section-only-e", "orphan-c",
                      "--reason", "clean link")
        assert res.returncode == 0, res.stderr
        # the new line must parse as a real edge (E -> C appears in the graph)
        nodes, edges, _problems = kb_graph.load_graph(root)
        assert (ENTRY_E, ENTRY_C) in {(s, d) for s, d, _k, _r in edges}, edges


LIN_A = "2026/08/20260801-100000-alice-design-old.md"
LIN_B = "2026/08/20260802-100000-alice-design-mid.md"
LIN_C = "2026/08/20260803-100000-alice-design-current.md"
LIN_D = "2026/08/20260804-100000-alice-mismatch.md"
LIN_E = "2026/08/20260805-100000-alice-no-successor.md"
LIN_G = "2026/08/20260806-100000-alice-cycle-g.md"
LIN_H = "2026/08/20260807-100000-alice-cycle-h.md"


def make_lineage_kb(base):
    """Supersede chain A→B→C plus every supersede lint defect: status
    mismatch and broken target (both on D), missing successor (E), and a
    two-entry cycle (G⇄H). C carries an amends link, E an extends link."""
    root = os.path.join(base, "repo", ".claude", "knowledge", "entries")
    os.makedirs(os.path.join(root, "2026", "08"))

    def write(relpath, title, status, superseded_by=None, links=""):
        lines = ["---", f"title: {title}", "created: 2026-08-01",
                 f"status: {status}"]
        if superseded_by:
            lines.append(f"superseded_by: {superseded_by}")
        lines += ['tags: "#design"', "---", "", f"Body of {title}.", ""]
        with open(os.path.join(root, relpath), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + links)

    write(LIN_A, "Design old", "superseded", superseded_by=LIN_B)
    write(LIN_B, "Design mid", "superseded", superseded_by=LIN_C)
    write(LIN_C, "Design current", "active",
          links=f"- amends: [Design old]({LIN_A}) — corrects the original scope\n")
    write(LIN_D, "Mismatch", "active", superseded_by="2026/08/nonexistent.md")
    write(LIN_E, "No successor", "superseded",
          links=f"- extends: [Design current]({LIN_C}) — elaborates the rollout\n")
    write(LIN_G, "Cycle G", "superseded", superseded_by=LIN_H)
    write(LIN_H, "Cycle H", "superseded", superseded_by=LIN_G)

    with open(os.path.join(base, "repo", ".claude", "knowledge", "CLAUDE.md"),
              "w", encoding="utf-8") as f:
        f.write("- #design — design lineage fixtures (7)\n")
    return root


def test_typed_edges_and_superseded_frontmatter():
    with tempfile.TemporaryDirectory() as base:
        root = make_lineage_kb(base)
        nodes, edges, problems = kb_graph.load_graph(root)
        kinds = {}
        for _s, _d, k, _r in edges:
            kinds[k] = kinds.get(k, 0) + 1
        assert kinds == {"superseded_by": 4, "amends": 1, "extends": 1}, kinds
        triples = {(s, d, k) for s, d, k, _r in edges}
        assert (LIN_A, LIN_B, "superseded_by") in triples, triples
        assert (LIN_C, LIN_A, "amends") in triples, triples
        assert (LIN_E, LIN_C, "extends") in triples, triples
        checks = {(nid, check) for nid, check, _ in problems}
        assert (LIN_D, "superseded-status-mismatch") in checks, checks
        assert (LIN_D, "superseded-broken") in checks, checks
        assert (LIN_E, "superseded-missing-successor") in checks, checks
        # D's broken superseded_by must not produce an edge
        assert not any(s == LIN_D for s, _d, _k, _r in edges), edges


def test_cli_lint_supersede_checks_and_cycle_scoping():
    with tempfile.TemporaryDirectory() as base:
        root = make_lineage_kb(base)
        res = run_cli(root, "--json", "lint")
        assert res.returncode == 1, (res.stdout, res.stderr)
        findings = json.loads(res.stdout)
        by_check = {}
        for f in findings:
            by_check.setdefault(f["check"], []).append(f["id"])
        assert by_check.get("superseded-status-mismatch") == [LIN_D], by_check
        assert by_check.get("superseded-broken") == [LIN_D], by_check
        assert by_check.get("superseded-missing-successor") == [LIN_E], by_check
        assert by_check.get("supersede-cycle") == [LIN_G, LIN_H], by_check
        assert len(findings) == 5, findings
        # cycle findings are reported per member, so file scoping still hits
        res = run_cli(root, "--json", "lint", os.path.join(root, LIN_G))
        scoped = json.loads(res.stdout)
        assert [(f["id"], f["check"]) for f in scoped] == \
            [(LIN_G, "supersede-cycle")], scoped


def test_cli_lineage():
    with tempfile.TemporaryDirectory() as base:
        root = make_lineage_kb(base)
        res = run_cli(root, "--json", "lineage", "design-old")
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert [s["id"] for s in data["successors"]] == [LIN_B, LIN_C], data
        assert data["current"]["id"] == LIN_C, data
        assert data["current"]["status"] == "active", data
        assert data["ancestors"] == [], data
        # reverse direction: the current entry knows what it replaced
        res = run_cli(root, "--json", "lineage", "design-current")
        data = json.loads(res.stdout)
        assert [a["id"] for a in data["ancestors"]] == [LIN_B, LIN_A], data
        assert data["current"]["id"] == LIN_C, data
        # text output is structure only — no body text may leak
        res = run_cli(root, "lineage", "design-old")
        assert res.returncode == 0 and "Body" not in res.stdout, res.stdout
        assert "current authority" in res.stdout, res.stdout
        # a supersede cycle must terminate, not hang
        res = run_cli(root, "--json", "lineage", "cycle-g")
        assert res.returncode == 0, res.stderr


def test_link_add_typed_kind():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        res = run_cli(root, "link-add", "topic-b", "orphan-c",
                      "--kind", "extends", "--reason", "builds on it")
        assert res.returncode == 0, (res.stdout, res.stderr)
        line = f"- extends: [Orphan C]({ENTRY_C}) — builds on it"
        assert line in read(root, ENTRY_B), read(root, ENTRY_B)
        nodes, edges, _problems = kb_graph.load_graph(root)
        assert (ENTRY_B, ENTRY_C, "extends") in \
            {(s, d, k) for s, d, k, _r in edges}, edges


ENTRY_F = "2026/07/20260704-100000-alice-malformed-f.md"
ENTRY_G = "2026/07/20260705-100000-alice-bracket-g.md"


def add_entry_f(root):
    """One valid link plus exactly one malformed line (bracket in label)."""
    with open(os.path.join(root, ENTRY_F), "w", encoding="utf-8") as f:
        f.write(f"""---
title: Malformed F
created: 2026-07-04
status: active
tags: "#pitfall"
---

Body F.

- see: [Topic A]({ENTRY_A}) — valid link
- see: [tool [sect] guide]({ENTRY_B}) — bracket in label
""")


def test_lint_malformed_link_exactly_one_with_line_number():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        _n0, edges0, problems0 = kb_graph.load_graph(root)
        add_entry_f(root)
        nodes, edges, problems = kb_graph.load_graph(root)
        mal = [(nid, det) for nid, chk, det in problems if chk == "malformed-link"]
        # exactly one finding, on F, pointing at the malformed line (line 11)
        assert len(mal) == 1 and mal[0][0] == ENTRY_F, mal
        assert mal[0][1].startswith("line 11:"), mal
        assert "[sect]" in mal[0][1], mal
        # the valid F→A link is the only new edge; the malformed line adds none
        pairs = {(s, d) for s, d, _k, _r in edges}
        assert (ENTRY_F, ENTRY_A) in pairs, pairs
        assert (ENTRY_F, ENTRY_B) not in pairs, pairs
        assert len(edges) == len(edges0) + 1, (len(edges0), len(edges))
        # no other finding changes
        assert len(problems) == len(problems0) + 1, (problems0, problems)
        # CLI surface: check name and exit code
        res = run_cli(root, "--json", "lint")
        assert res.returncode == 1, res.stdout
        hits = [f for f in json.loads(res.stdout) if f["check"] == "malformed-link"]
        assert [f["id"] for f in hits] == [ENTRY_F], hits


def test_link_add_refuses_bracket_title():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        with open(os.path.join(root, ENTRY_G), "w", encoding="utf-8") as f:
            f.write("""---
title: Guide [draft]
created: 2026-07-05
status: active
tags: "#pitfall"
---

Body G.

## 関連

""")
        before = read(root, ENTRY_B)
        res = run_cli(root, "link-add", "topic-b", "bracket-g", "--reason", "x")
        assert res.returncode != 0, "bracket title must be refused"
        assert "square bracket" in res.stderr, res.stderr
        assert read(root, ENTRY_B) == before, "src file must be untouched"
        # bidirectional validation must refuse before writing either side
        before_g = read(root, ENTRY_G)
        res = run_cli(root, "link-add", "bracket-g", "topic-b",
                      "--reason", "x", "--bidirectional")
        assert res.returncode != 0, res.stdout
        assert read(root, ENTRY_G) == before_g and read(root, ENTRY_B) == before


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
