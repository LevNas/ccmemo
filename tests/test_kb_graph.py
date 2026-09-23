#!/usr/bin/env python3
"""Dependency-free self-tests for scripts/kb_graph.py.

Run: python3 tests/test_kb_graph.py   (exit 0 = all pass)

Builds a synthetic knowledge base in a temp dir shaped like a real project
(<repo>/.claude/knowledge/entries/YYYY/MM/...) so link resolution and the
broken-link vs out-of-tree distinction behave as in production.
"""

import json
import os
import re
import shutil
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
            f.write('---\ntitle: Clean\ntags: "#pitfall"\n'
                    'description: "Open this when a fixture needs a trigger condition '
                    'long enough to pass the length floor of the lint."\n'
                    '---\n\nNothing wrong here.\n')
        with open(os.path.join(base, "repo", ".claude", "knowledge", "CLAUDE.md"), "w",
                  encoding="utf-8") as f:
            f.write("- #pitfall — recurring traps (1)\n")
        res = run_cli(root, "lint")
        assert res.returncode == 0, (res.stdout, res.stderr)


ENTRY_AM = "2026/07/20260704-100000-alice-amends-f.md"
ENTRY_EX = "2026/07/20260704-110000-alice-extends-g.md"


def test_lint_description_labels_and_reciprocity():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)

        def write(rel, text):
            with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
                f.write(text)

        # F amends A without A linking back; short description; unlabeled see.
        write(ENTRY_AM, f"""---
title: Amends F
created: 2026-07-04
status: active
tags: "#pitfall"
description: "too short"
---

Body F.

- amends: [Topic A]({ENTRY_A}) — corrects one paragraph of A
- see: [Orphan C]({ENTRY_C})
""")
        # G extends B, and B is superseded by G: reciprocity via superseded_by.
        write(ENTRY_EX, f"""---
title: Extends G
created: 2026-07-04
status: active
tags: "#pitfall"
description: "{'x' * 400}"
---

Body G.

- extends: [Topic B]({ENTRY_B}) — develops B
""")
        with open(os.path.join(root, ENTRY_B), encoding="utf-8") as f:
            b = f.read()
        write(ENTRY_B, b.replace("status: active\n", f"status: superseded\nsuperseded_by: {ENTRY_EX}\n", 1))

        nodes, _edges, problems = kb_graph.load_graph(root)
        checks = {(nid, check) for nid, check, _ in problems}
        assert (ENTRY_A, "missing-description") in checks, checks
        assert (ENTRY_AM, "description-length") in checks, checks
        assert (ENTRY_EX, "description-length") in checks, checks
        assert (ENTRY_AM, "unlabeled-link") in checks, checks
        assert (ENTRY_AM, "amends-unreciprocated") in checks, checks
        assert (ENTRY_EX, "extends-unreciprocated") not in checks, checks
        assert nodes[ENTRY_B]["superseded_by"] == ENTRY_EX, nodes[ENTRY_B]
        # The labeled amends line itself is not reported as unlabeled.
        unl = [d for n, c, d in problems if n == ENTRY_AM and c == "unlabeled-link"]
        assert unl == [f"see: ({ENTRY_C}) has no “— why” label"], unl
        # Scoped lint on F still surfaces its own findings (post-write hook usage).
        res = run_cli(root, "--json", "lint", os.path.join(root, ENTRY_AM))
        scoped = {f["check"] for f in json.loads(res.stdout)}
        assert {"description-length", "unlabeled-link", "amends-unreciprocated"} <= scoped, scoped


def test_lint_schema_gate_declaration_env_and_flag():
    """Schema-2 checks are advisory on an undeclared corpus and enforced once
    `schema_version: 2` is declared in <root>/../CLAUDE.md (or via --schema /
    CCMEMO_SCHEMA_VERSION); other checks are enforced regardless."""
    with tempfile.TemporaryDirectory() as base:
        root = os.path.join(base, "repo", ".claude", "knowledge", "entries")
        os.makedirs(root)
        registry = os.path.join(base, "repo", ".claude", "knowledge", "CLAUDE.md")
        with open(os.path.join(root, "20260701-100000-alice-nodesc.md"), "w",
                  encoding="utf-8") as f:
            f.write('---\ntitle: No description\ntags: "#pitfall"\n---\n\nBody.\n')
        with open(registry, "w", encoding="utf-8") as f:
            f.write("# KB\n\n- #pitfall — recurring traps (1)\n")

        def lint(*extra, env=None):
            e = dict(os.environ)
            e.pop("CCMEMO_SCHEMA_VERSION", None)
            if env:
                e.update(env)
            return subprocess.run([sys.executable, KB_GRAPH, "--root", root, "--json",
                                   *extra, "lint"], capture_output=True, text=True,
                                  timeout=30, env=e)

        # undeclared corpus: advisory only, exit 0
        res = lint()
        assert res.returncode == 0, (res.stdout, res.stderr)
        found = {(f["check"], f["severity"]) for f in json.loads(res.stdout)}
        assert ("missing-description", "advisory") in found, found
        # --schema 2 enforces; the schema-3 checks stay advisory
        res = lint("--schema", "2")
        assert res.returncode == 1, res.stdout
        assert {(f["check"], f["severity"]) for f in json.loads(res.stdout)} == \
            {("missing-description", "error"), ("missing-id", "advisory"),
             ("missing-generated", "advisory")}, res.stdout
        # --schema 3 enforces those too
        res = lint("--schema", "3")
        assert {f["check"] for f in json.loads(res.stdout) if f["severity"] == "error"} == \
            {"missing-description", "missing-id", "missing-generated"}, res.stdout
        # env overrides the (missing) declaration
        res = lint(env={"CCMEMO_SCHEMA_VERSION": "2"})
        assert res.returncode == 1, res.stdout
        # declaration in CLAUDE.md frontmatter enforces; --schema 1 relaxes it
        with open(registry, "w", encoding="utf-8") as f:
            f.write("---\nschema_version: 2\n---\n# KB\n\n- #pitfall — recurring traps (1)\n")
        res = lint()
        assert res.returncode == 1, res.stdout
        assert kb_graph.kb_schema_version(root) == 2
        res = lint("--schema", "1")
        assert res.returncode == 0, res.stdout
        # text output names the advisory block and the declaration to raise
        res = subprocess.run([sys.executable, KB_GRAPH, "--root", root, "--schema", "1", "lint"],
                             capture_output=True, text=True, timeout=30)
        assert "advisory" in res.stdout and "schema_version" in res.stdout, res.stdout
        assert res.stdout.rstrip().endswith("0 finding(s), 3 advisory"), res.stdout
        # a non-schema check is enforced even on an undeclared corpus
        with open(os.path.join(root, "20260701-100000-alice-nodesc.md"), "a",
                  encoding="utf-8") as f:
            f.write("\n- see: [gone](2026/07/nope.md) — broken\n")
        res = lint("--schema", "1")
        assert res.returncode == 1, res.stdout
        assert {f["check"] for f in json.loads(res.stdout) if f["severity"] == "error"} == \
            {"broken-link"}, res.stdout


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
        lines += ['tags: "#design"',
                  f'description: "Open when a lineage fixture named {title} needs a '
                  'trigger condition long enough for the length floor."',
                  "---", "", f"Body of {title}.", ""]
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
        # the lineage fixture predates schema 3: drop its id/generated advisories
        findings = [f for f in json.loads(res.stdout)
                    if f["check"] not in ("missing-id", "missing-generated")]
        by_check = {}
        for f in findings:
            by_check.setdefault(f["check"], []).append(f["id"])
        assert by_check.get("superseded-status-mismatch") == [LIN_D], by_check
        assert by_check.get("superseded-broken") == [LIN_D], by_check
        assert by_check.get("superseded-missing-successor") == [LIN_E], by_check
        assert by_check.get("supersede-cycle") == [LIN_G, LIN_H], by_check
        # E extends C but C neither links back nor is superseded by E;
        # C amends A and A's supersede chain (A→B→C) ends at C, so that one is fine.
        assert by_check.get("extends-unreciprocated") == [LIN_E], by_check
        assert "amends-unreciprocated" not in by_check, by_check
        assert len(findings) == 6, findings
        # cycle findings are reported per member, so file scoping still hits
        res = run_cli(root, "--json", "lint", os.path.join(root, LIN_G))
        scoped = [f for f in json.loads(res.stdout)
                  if f["check"] not in ("missing-id", "missing-generated")]
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
        # no other finding changes: F adds exactly malformed-link and (having
        # no description: line, which would shift the line number under test)
        # missing-description, plus the schema-3 missing-id / missing-generated
        assert len(problems) == len(problems0) + 4, (problems0, problems)
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


def test_supersede_marks_frontmatter_banner_and_backlink():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        add_entry_e(root)
        res = run_cli(root, "supersede", "topic-a", "section-only-e",
                      "--reason", "replaced by consolidated entry",
                      "--date", "2026-08-16")
        assert res.returncode == 0, (res.stdout, res.stderr)
        text_a = read(root, ENTRY_A)
        # status/superseded_by as an adjacent frontmatter pair, old status gone
        assert f"status: superseded\nsuperseded_by: {ENTRY_E}\n" in text_a, text_a
        assert "status: active" not in text_a, text_a
        # banner directly under the closing frontmatter delimiter
        banner = (f"> **⚠ superseded (2026-08-16)** — current: "
                  f"[Section Only E]({ENTRY_E})")
        assert f"---\n\n{banner}\n" in text_a, text_a
        # amends back-link appended to the replacement (heading anchor)
        line = f"- amends: [Topic A]({ENTRY_A}) — replaced by consolidated entry"
        assert line in read(root, ENTRY_E), read(root, ENTRY_E)
        # the result must not trip any supersede lint check
        res = run_cli(root, "lint")
        assert "superseded-" not in res.stdout and "supersede-" not in res.stdout, res.stdout


def test_supersede_existing_see_link_counts_as_backlink():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        # fixture B already carries "- see: [Topic A]" -> no amends duplicate
        before_b = read(root, ENTRY_B)
        res = run_cli(root, "supersede", "topic-a", "topic-b",
                      "--reason", "r", "--date", "2026-08-16")
        assert res.returncode == 0, (res.stdout, res.stderr)
        assert "already linked" in res.stdout, res.stdout
        assert read(root, ENTRY_B) == before_b, "no near-duplicate link line"
        assert "status: superseded" in read(root, ENTRY_A)


def test_supersede_is_idempotent_and_self_healing():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        add_entry_e(root)
        args = ("supersede", "topic-a", "section-only-e",
                "--reason", "r", "--date", "2026-08-16")
        assert run_cli(root, *args).returncode == 0
        after_a, after_e = read(root, ENTRY_A), read(root, ENTRY_E)
        # full rerun: recognised, nothing rewritten
        res = run_cli(root, *args)
        assert res.returncode == 0 and "already superseded" in res.stdout, res.stdout
        assert read(root, ENTRY_A) == after_a and read(root, ENTRY_E) == after_e
        # interrupted-run repair: back-link missing -> only that piece is redone
        stripped = "\n".join(l for l in after_e.splitlines()
                             if "- amends:" not in l) + "\n"
        with open(os.path.join(root, ENTRY_E), "w", encoding="utf-8") as f:
            f.write(stripped)
        res = run_cli(root, *args)
        assert res.returncode == 0 and "back-linked" in res.stdout, res.stdout
        assert read(root, ENTRY_A) == after_a, "old entry must stay untouched"
        assert read(root, ENTRY_E).count("- amends:") == 1


def test_supersede_refuses_cycle_and_conflicting_successor():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        assert run_cli(root, "supersede", "topic-a", "topic-b",
                       "--reason", "r", "--date", "2026-08-16").returncode == 0
        # reverse direction would close a cycle
        res = run_cli(root, "supersede", "topic-b", "topic-a",
                      "--reason", "r", "--date", "2026-08-16")
        assert res.returncode != 0 and "cycle" in res.stderr, res.stderr
        # a second, different successor must not silently overwrite the first
        before_a = read(root, ENTRY_A)
        res = run_cli(root, "supersede", "topic-a", "orphan-c",
                      "--reason", "r", "--date", "2026-08-16")
        assert res.returncode != 0 and "already superseded by" in res.stderr, res.stderr
        assert read(root, ENTRY_A) == before_a


def test_supersede_no_partial_application_without_backlink_anchor():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        before_a = read(root, ENTRY_A)
        # orphan-c has no link line and no 関連 heading -> back-link cannot be
        # planned -> the old entry must not be written either
        res = run_cli(root, "supersede", "topic-a", "orphan-c",
                      "--reason", "r", "--date", "2026-08-16")
        assert res.returncode != 0 and "manually" in res.stderr, res.stderr
        assert read(root, ENTRY_A) == before_a, "no partial application"


def test_supersede_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        before_a, before_b = read(root, ENTRY_A), read(root, ENTRY_B)
        res = run_cli(root, "supersede", "topic-a", "topic-b",
                      "--reason", "r", "--date", "2026-08-16", "--dry-run")
        assert res.returncode == 0 and "dry-run" in res.stdout, res.stdout
        assert read(root, ENTRY_A) == before_a and read(root, ENTRY_B) == before_b


def test_supersede_refuses_bracket_replacement_title():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        with open(os.path.join(root, ENTRY_G), "w", encoding="utf-8") as f:
            f.write('---\ntitle: Guide [draft]\ncreated: 2026-07-05\n'
                    'status: active\ntags: "#pitfall"\n---\n\nBody G.\n\n## 関連\n\n')
        before_a = read(root, ENTRY_A)
        res = run_cli(root, "supersede", "topic-a", "bracket-g",
                      "--reason", "r", "--date", "2026-08-16")
        assert res.returncode != 0 and "square bracket" in res.stderr, res.stderr
        assert read(root, ENTRY_A) == before_a


# --------------------------------------------------------------------------- #
# Schema 3: migrate / verify / rename / relink and the trust-family lint checks
# --------------------------------------------------------------------------- #

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _fm_lines(root, rel):
    text = read(root, rel)
    end = text.find("\n---", 3)
    return text[4:end].splitlines()


def test_schema3_migrate_is_idempotent_and_minimal():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        before_c = read(root, ENTRY_C)
        res = run_cli(root, "migrate", "--to", "3", "--tz", "+09:00", "--dry-run")
        assert res.returncode == 0 and "would add id, generated" in res.stdout, (res.stdout, res.stderr)
        assert read(root, ENTRY_C) == before_c  # dry-run writes nothing
        res = run_cli(root, "migrate", "--to", "3", "--tz", "+09:00")
        assert res.returncode == 0, (res.stdout, res.stderr)
        assert "skip (no frontmatter): " + ENTRY_D in res.stdout, res.stdout
        assert "id added to 3, generated added to 3" in res.stdout, res.stdout
        lines = _fm_lines(root, ENTRY_A)
        assert lines[0] == "title: Topic A" and lines[1].startswith("id: "), lines
        assert UUID_RE.match(lines[1][4:]), lines[1]
        # generated sits right after created:, at = filename timestamp in the corpus TZ
        i = lines.index("created: 2026-07-01")
        assert lines[i + 1:i + 4] == ["generated:", "  by: claude-code",
                                      "  at: 2026-07-01T10:00:00+09:00"], lines
        # nothing else moved; body intact
        assert read(root, ENTRY_A).endswith("escapes the repo\n")
        nodes, _e, problems = kb_graph.load_graph(root)
        assert nodes[ENTRY_A]["generated"] == {"by": "claude-code", "at": "2026-07-01T10:00:00+09:00"}
        s3 = [(n, c) for n, c, _d in problems if c in ("missing-id", "missing-generated")]
        assert s3 == [(ENTRY_D, "missing-id"), (ENTRY_D, "missing-generated")], s3
        ids = {nodes[r]["id"] for r in (ENTRY_A, ENTRY_B, ENTRY_C)}
        assert len(ids) == 3, ids
        # verified is never backfilled
        assert "verified" not in read(root, ENTRY_A)
        # second run: no change at all
        snapshot = {r: read(root, r) for r in (ENTRY_A, ENTRY_B, ENTRY_C)}
        res = run_cli(root, "migrate", "--to", "3", "--tz", "+09:00")
        assert res.returncode == 0 and "changed 0 entr" in res.stdout, res.stdout
        assert {r: read(root, r) for r in (ENTRY_A, ENTRY_B, ENTRY_C)} == snapshot
        # a custom actor, an unknown target schema
        assert run_cli(root, "migrate", "--to", "4").returncode != 0
        assert run_cli(root, "migrate", "--to", "3", "--by", "bob").returncode != 0


def test_schema3_verify_appends_events_and_derives_tier():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        assert run_cli(root, "migrate", "--to", "3", "--tz", "+09:00").returncode == 0
        res = run_cli(root, "verify", "topic-a", "--by", "human:alice",
                      "--at", "2026-07-05T10:00:00+09:00")
        assert res.returncode == 0 and "(tier: human)" in res.stdout, (res.stdout, res.stderr)
        text = read(root, ENTRY_A)
        assert "verified:\n  - by: human:alice\n    at: 2026-07-05T10:00:00+09:00\n" in text, text
        assert text.index("generated:") < text.index("verified:") < text.index("status:"), text
        nodes, _e, problems = kb_graph.load_graph(root)
        assert nodes[ENTRY_A]["verified_tier"] == "human"
        assert not [p for p in problems if p[1] in ("invalid-actor", "verification-expired")], problems
        # same event again: idempotent
        res = run_cli(root, "verify", "topic-a", "--by", "human:alice", "--at", "2026-07-05T10:00:00+09:00")
        assert "already recorded" in res.stdout and read(root, ENTRY_A) == text
        # a later machine check becomes the latest verifier
        res = run_cli(root, "verify", "topic-a", "--by", "claude-code/claude-fable-5-1",
                      "--at", "2026-07-06T10:00:00+09:00")
        assert res.returncode == 0 and "(tier: machine)" in res.stdout, res.stdout
        nodes, _e, _p = kb_graph.load_graph(root)
        assert nodes[ENTRY_A]["verified_tier"] == "machine"
        assert nodes[ENTRY_A]["verified_at"] == "2026-07-06T10:00:00+09:00"
        # an event older than generated.at is recorded but flagged as expired
        res = run_cli(root, "verify", "topic-b", "--by", "process:gate", "--at", "2026-06-01T00:00:00+09:00")
        assert res.returncode == 0 and "expired" in res.stderr, (res.stdout, res.stderr)
        nodes, _e, problems = kb_graph.load_graph(root)
        assert nodes[ENTRY_B]["verified_tier"] == ""
        assert (ENTRY_B, "verification-expired") in [(p[0], p[1]) for p in problems], problems
        # invalid actor / time: nothing written
        before = read(root, ENTRY_C)
        assert run_cli(root, "verify", "orphan-c", "--by", "carol").returncode != 0
        assert run_cli(root, "verify", "orphan-c", "--by", "human:carol", "--at", "soon").returncode != 0
        res = run_cli(root, "verify", "orphan-c", "--by", "human:carol", "--dry-run")
        assert res.returncode == 0 and "dry-run" in res.stdout
        assert read(root, ENTRY_C) == before
        # default --at is now, with an offset
        res = run_cli(root, "verify", "orphan-c", "--by", "human:carol")
        assert res.returncode == 0, res.stderr
        nodes, _e, _p = kb_graph.load_graph(root)
        assert re.search(r"[+-]\d{2}:\d{2}$", nodes[ENTRY_C]["verified_at"]), nodes[ENTRY_C]


def test_schema3_rename_changes_slug_only_and_rewrites_links():
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        assert run_cli(root, "migrate", "--to", "3", "--tz", "+09:00").returncode == 0
        # C: superseded_by A (root-relative); E: directory-relative link to A
        with open(os.path.join(root, ENTRY_C), "a", encoding="utf-8") as f:
            pass
        c_text = read(root, ENTRY_C).replace("status: active", f"status: superseded\nsuperseded_by: {ENTRY_A}")
        with open(os.path.join(root, ENTRY_C), "w", encoding="utf-8") as f:
            f.write(c_text)
        add_entry_e(root)
        with open(os.path.join(root, ENTRY_E), "a", encoding="utf-8") as f:
            f.write(f"- see: [Topic A]({os.path.basename(ENTRY_A)}) — dir-relative on purpose\n")
        nodes, _e, _p = kb_graph.load_graph(root)
        old_id = nodes[ENTRY_A]["id"]
        new_rel = "2026/07/20260701-100000-alice-docker-lessons.md"

        res = run_cli(root, "rename", "topic-a", "docker-lessons", "--dry-run")
        assert res.returncode == 0 and f"would rename {ENTRY_A} -> {new_rel}" in res.stdout, res.stdout
        assert os.path.exists(os.path.join(root, ENTRY_A))
        res = run_cli(root, "rename", "topic-a", "docker-lessons")
        assert res.returncode == 0, (res.stdout, res.stderr)
        assert not os.path.exists(os.path.join(root, ENTRY_A)) and os.path.exists(os.path.join(root, new_rel))
        assert f"relinked: {ENTRY_B}" in res.stdout and f"relinked: {ENTRY_C}" in res.stdout \
            and f"relinked: {ENTRY_E}" in res.stdout, res.stdout
        assert f"({new_rel})" in read(root, ENTRY_B) and f"({ENTRY_A})" not in read(root, ENTRY_B)
        assert f"superseded_by: {new_rel}" in read(root, ENTRY_C)
        assert f"({os.path.basename(new_rel)}) — dir-relative" in read(root, ENTRY_E), read(root, ENTRY_E)
        nodes, edges, problems = kb_graph.load_graph(root)
        assert nodes[new_rel]["id"] == old_id  # identity survives the rename
        stale = [p for p in problems if p[1] in ("broken-link", "superseded-broken")
                 and os.path.basename(ENTRY_A) in p[2]]
        assert not stale, stale  # (A's own pre-existing broken ref is unrelated)
        assert (ENTRY_B, new_rel) in {(s, d) for s, d, _k, _r in edges}
        # refusals
        assert run_cli(root, "rename", "docker-lessons", "Bad_Slug").returncode != 0
        assert run_cli(root, "rename", "docker-lessons", "orphan-c").returncode == 0  # different prefix: ok
        assert run_cli(root, "rename", "notes", "x").returncode != 0  # not a dated name


def test_schema3_relink_repairs_links_recorded_as_moves():
    import sqlite3
    with tempfile.TemporaryDirectory() as base:
        root = make_kb(base)
        assert run_cli(root, "migrate", "--to", "3", "--tz", "+09:00").returncode == 0
        nodes, _e, _p = kb_graph.load_graph(root)
        # A was moved by hand (mv), B still links the old path
        moved = "2026/07/20260701-100000-alice-moved-a.md"
        os.rename(os.path.join(root, ENTRY_A), os.path.join(root, moved))
        db_dir = os.path.join(root, "..", ".index")
        os.makedirs(db_dir)
        conn = sqlite3.connect(os.path.join(db_dir, "kb.db"))
        conn.execute("CREATE TABLE moves (id TEXT, old_relpath TEXT, new_relpath TEXT, at INTEGER)")
        conn.execute("INSERT INTO moves VALUES (?, ?, ?, 0)", (nodes[ENTRY_A]["id"], ENTRY_A, moved))
        conn.commit()
        conn.close()
        _n, _e, problems = kb_graph.load_graph(root)
        assert (ENTRY_B, "broken-link") in [(p[0], p[1]) for p in problems]
        res = run_cli(root, "relink", "--dry-run")
        assert res.returncode == 0 and f"would relink {ENTRY_B}" in res.stdout, res.stdout
        assert f"({ENTRY_A})" in read(root, ENTRY_B)
        res = run_cli(root, "relink")
        assert res.returncode == 0 and f"relinked {ENTRY_B}: {ENTRY_A} -> {moved}" in res.stdout, res.stdout
        assert f"({moved})" in read(root, ENTRY_B)
        _n, _e, problems = kb_graph.load_graph(root)
        stale = [p for p in problems if p[1] == "broken-link" and os.path.basename(ENTRY_A) in p[2]]
        assert not stale, stale
        res = run_cli(root, "relink")
        assert "nothing to relink" in res.stdout
        # no index at all
        shutil.rmtree(db_dir)
        assert run_cli(root, "relink").returncode != 0


def test_schema3_lint_checks_and_severities():
    with tempfile.TemporaryDirectory() as base:
        root = os.path.join(base, "repo", ".claude", "knowledge", "entries", "2026", "07")
        os.makedirs(root)
        root = os.path.dirname(os.path.dirname(root))
        gen = "generated:\n  by: human:alice\n  at: 2026-07-01T10:00:00+09:00\n"
        desc = 'description: "Open when the schema-3 lint fixture needs a trigger condition long enough to pass."\n'
        entries = {
            "2026/07/20260701-100000-alice-dup1.md":
                "---\ntitle: Same title\nid: 11111111-1111-4111-8111-111111111111\n" + gen + desc + "---\n\nx\n",
            "2026/07/20260701-100001-alice-dup2.md":
                "---\ntitle: same  title\nid: 11111111-1111-4111-8111-111111111111\n" + gen + desc + "---\n\nx\n",
            "2026/07/20260701-100002-alice-actor.md":
                "---\ntitle: Bad actor\nid: 22222222-2222-4222-8222-222222222222\n"
                "generated: {by: bob, at: 2026-07-01T10:00:00+09:00}\n" + desc + "---\n\nx\n",
            "2026/07/20260701-100003-alice-expired.md":
                "---\ntitle: Expired\nid: 33333333-3333-4333-8333-333333333333\n" + gen +
                "verified:\n  - by: human:bob\n    at: 2026-06-01T10:00:00+09:00\n" + desc + "---\n\nx\n",
            "2026/07/20260701-100004-alice-stale.md":
                "---\ntitle: Stale\nid: 44444444-4444-4444-8444-444444444444\n" + gen +
                "stale_after: 2020-01-01\n" + desc + "---\n\nx\n",
            "2026/07/20260701-100005-alice-badgen.md":
                "---\ntitle: Bad generated\nid: 55555555-5555-4555-8555-555555555555\n"
                "generated: yesterday\n" + desc + "---\n\nx\n",
            "2026/07/20260701-100006-alice-badver.md":
                "---\ntitle: Bad verifier\nid: 66666666-6666-4666-8666-666666666666\n" + gen +
                "verified:\n  - by: lint\n    at: 2026-07-02T10:00:00+09:00\n" + desc + "---\n\nx\n",
        }
        for rel, text in entries.items():
            with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
                f.write(text)
        with open(os.path.join(root, "..", "CLAUDE.md"), "w", encoding="utf-8") as f:
            f.write("---\nschema_version: 3\n---\n# KB\n")
        res = run_cli(root, "--json", "lint")
        assert res.returncode == 1, (res.stdout, res.stderr)
        sev = {(os.path.basename(f["id"])[22:], f["check"], f["severity"]) for f in json.loads(res.stdout)}
        expected = {
            ("dup1.md", "duplicate-id", "error"), ("dup2.md", "duplicate-id", "error"),
            ("dup1.md", "duplicate-title", "advisory"), ("dup2.md", "duplicate-title", "advisory"),
            ("actor.md", "invalid-actor", "error"),
            ("expired.md", "verification-expired", "advisory"),
            ("stale.md", "stale-after-passed", "advisory"),
            ("badgen.md", "missing-generated", "error"),
            ("badver.md", "invalid-actor", "error"),
        }
        assert sev == expected, sev ^ expected
        # text output: the two advisory blocks are distinct
        res = run_cli(root, "lint")
        assert "informational at every schema_version" in res.stdout, res.stdout
        assert "enforced at a later schema_version" not in res.stdout, res.stdout
        # below schema 3 the identity/actor checks are gated, the notices stay advisory
        res = run_cli(root, "--json", "--schema", "2", "lint")
        assert res.returncode == 0, res.stdout
        assert {f["severity"] for f in json.loads(res.stdout)} == {"advisory"}
        res = run_cli(root, "--schema", "2", "lint")
        assert "enforced at a later schema_version (latest: 3)" in res.stdout, res.stdout
        assert "informational at every schema_version" in res.stdout, res.stdout


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
