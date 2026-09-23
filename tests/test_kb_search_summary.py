#!/usr/bin/env python3
"""Self-tests for the typed-edge index columns and the search summary mode.

Run: python3 tests/test_kb_search_summary.py   (exit 0 = all pass)

The parsing / formatting half is pure stdlib. The sqlite half (schema v2
upgrade of an older index without re-embedding) runs only when `sqlite_vec`
is importable — e.g. `uv run --with sqlite-vec --no-project python3
tests/test_kb_search_summary.py` — and is reported as skipped otherwise, so
the suite never needs a dependency the hooks themselves do not have.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import kb_index as kbi  # noqa: E402
import kb_search as ks  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


A = "2026/09/20260901-100000-u-alpha.md"
B = "2026/09/20260902-100000-u-beta.md"
C = "2026/08/20260801-100000-u-gamma.md"


def make_kb(base):
    root = Path(base) / "entries"
    for rel, text in {
        A: (
            "---\n"
            "title: Alpha entry\n"
            "status: active\n"
            'tags: "#x"\n'
            "description: Open when choosing between alpha and beta.\n"
            "---\n\n# Alpha entry\n\nLead paragraph of alpha.\n\n## 関連\n\n"
            f"- see: [Beta](../../{B}) — why beta matters\n"
            f"- amends: [Gamma]({C})\n"
            "- ref: [Ext](https://example.com/) — skipped\n"
            "- see: [Missing](2026/09/nope.md) — broken link kept raw\n"
        ),
        B: (
            "---\ntitle: Beta entry\nstatus: superseded\n"
            f"superseded_by: {A}\n---\n\n# Beta entry\n\n"
            "- not a lead (list)\n\nThe real lead of beta.\n\n"
            f"- extends: [Alpha]({A}) — beta extends alpha\n"
        ),
        C: "---\ntitle: Gamma entry\n---\n\n# Gamma entry\n\n## Only headings\n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_parse_entry():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_kb(tmp)
        a = kbi.parse_entry(root / A, root)
        check("description parsed", a.description == "Open when choosing between alpha and beta.")
        rels = [(e["rel"], e["target"]) for e in a.edges]
        check("edges typed, external skipped, broken kept raw",
              rels == [("see", B), ("amends", C), ("see", "2026/09/nope.md")], repr(rels))
        check("dir-relative target normalised to root relpath", a.edges[0]["target"] == B)
        check("label stored", a.edges[0]["label"] == "why beta matters")
        check("see column = all edge targets", a.see == [B, C, "2026/09/nope.md"])
        check("lead paragraph", ks.make_lead(root / A) == "Lead paragraph of alpha.")
        check("lead skips list lines", ks.make_lead(root / B) == "The real lead of beta.")
        check("lead empty when only headings", ks.make_lead(root / C) == "")
        check("snippet drops H1", not ks.make_snippet(root / A, "zzz").startswith("#"))


def test_entry_id_and_format():
    check("entry_id prefix", ks.entry_id(A) == "20260901-100000")
    check("entry_id fallback basename", ks.entry_id("design/spec.md") == "spec.md")
    results = [{
        "title": "Alpha entry", "relpath": A, "status": "active",
        "description": "Open when choosing.", "description_source": "frontmatter",
        "snippet": "s",
        "edges": [
            {"target": B, "rel": "see", "label": "why beta matters", "title": "Beta entry"},
            {"target": C, "rel": "amends", "label": "", "title": "Gamma entry"},
        ],
        "edges_total": 3,
        "linked_from": [{"source": B, "rel": "extends", "label": "beta extends alpha", "title": "Beta entry"}],
        "linked_from_total": 2,
    }, {
        "title": "Beta entry", "relpath": B, "status": "superseded",
        "description": "The real lead of beta.", "description_source": "lead", "snippet": "",
        "edges": [], "edges_total": 0, "linked_from": [], "linked_from_total": 4,
    }]
    out = ks.format_summary(results)
    lines = out.splitlines()
    check("index.md shape", lines[0] == f"* [Alpha entry]({A}) - Open when choosing.", lines[0])
    check("edge with label", lines[1] == "  - see 20260902-100000 — why beta matters", lines[1])
    check("edge without label shows title", lines[2] == "  - amends 20260801-100000 — Gamma entry (+1)", lines[2])
    check("incoming edge", lines[3] == "  - ← extends 20260902-100000 — beta extends alpha (+1)", lines[3])
    check("superseded flagged + lead marked",
          lines[4] == f"* [Beta entry]({B}) (superseded) - (lead) The real lead of beta.", lines[4])
    check("incoming count when none shown", lines[5] == "  - (← +4 incoming)", lines[5])
    check("empty", ks.format_summary([]) == "(no hits)")
    long = "x" * 200
    check("truncation", ks._trunc(long, 10) == "x" * 9 + "…")


def test_schema_upgrade():
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        print("skip schema upgrade test (sqlite_vec not importable)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = make_kb(tmp)
        db = kbi.index_db_path(root)
        conn = kbi.connect(db)
        # A v1 index: no description column, no edges table, no schema_version.
        conn.execute("CREATE TABLE entries (relpath TEXT PRIMARY KEY, title TEXT, tags TEXT, "
                     "status TEXT, created TEXT, type TEXT, see TEXT, sha256 TEXT)")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        for rel in (A, B, C):
            e = kbi.parse_entry(root / rel, root)
            conn.execute("INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (rel, "old title", "[]", "active", "", "", "[]", e.sha256))
        conn.commit()
        conn.close()

        n = kbi.ensure_metadata(root)
        check("upgrade touched every stored entry", n == 3, n)
        conn = kbi.connect(db)
        desc = conn.execute("SELECT description FROM entries WHERE relpath = ?", (A,)).fetchone()[0]
        check("description backfilled", desc == "Open when choosing between alpha and beta.")
        title = conn.execute("SELECT title FROM entries WHERE relpath = ?", (A,)).fetchone()[0]
        check("title refreshed", title == "Alpha entry")
        rows = conn.execute("SELECT target, rel, label FROM edges WHERE src = ? ORDER BY ord", (A,)).fetchall()
        check("edges backfilled", rows == [(B, "see", "why beta matters"), (C, "amends", ""),
                                            ("2026/09/nope.md", "see", "broken link kept raw")], rows)
        ver = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        check("schema_version stamped", ver == str(kbi.SCHEMA_VERSION))
        nchunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        check("no embedding during upgrade", nchunks == 0)
        conn.close()
        check("second call is a no-op", kbi.ensure_metadata(root) == 0)

        # Edge lookups used by the summary mode.
        meta = ks._entry_meta(root)
        info = ks.entry_edges(root, [A, B], meta, max_out=1, max_in=-1)
        check("outgoing capped, total kept", len(info[A]["edges"]) == 1 and info[A]["edges_total"] == 3)
        check("neighbour title resolved", info[A]["edges"][0]["title"] == "Beta entry")
        check("incoming resolved", info[A]["linked_from"] == [
            {"source": B, "rel": "extends", "label": "beta extends alpha", "title": "Beta entry"}])
        fused = {A: 1.0}
        ks.expand_see_one_hop(root, [A], fused)
        check("one-hop follows amends too", fused.get(C) == 0.5 and fused.get(B) == 0.5, fused)
        check("one-hop ignores unresolved target", "2026/09/nope.md" not in fused)


if __name__ == "__main__":
    test_parse_entry()
    test_entry_id_and_format()
    test_schema_upgrade()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall passed")
