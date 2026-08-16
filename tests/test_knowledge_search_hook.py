#!/usr/bin/env python3
"""Self-tests for hooks/userpromptsubmit_knowledge_search.sh status filtering.

Run: python3 tests/test_knowledge_search_hook.py   (exit 0 = all pass)

The hook needs bash, jq and rg on PATH (its own runtime dependencies); mecab
is replaced with a stub executable that emits fixed noun lines, so the tests
are deterministic and do not require a mecab installation. All fixture
entries are synthetic.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HOOK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "hooks",
    "userpromptsubmit_knowledge_search.sh",
)

MECAB_STUB = """#!/usr/bin/env bash
# mecab stub: ignore stdin, emit fixed noun lines in mecab output format.
cat >/dev/null
printf 'alphaword\\t名詞,一般,*,*,*,*,*\\n'
printf 'betaword\\t名詞,一般,*,*,*,*,*\\n'
printf 'EOS\\n'
"""


def _make_workdir():
    """Create a tempdir with an entries tree and a stubbed-mecab bin dir."""
    workdir = tempfile.mkdtemp(prefix="ccmemo-ks-")
    os.makedirs(os.path.join(workdir, ".claude", "knowledge", "entries", "2026"))
    bindir = os.path.join(workdir, "stub-bin")
    os.makedirs(bindir)
    mecab = os.path.join(bindir, "mecab")
    with open(mecab, "w", encoding="utf-8") as f:
        f.write(MECAB_STUB)
    os.chmod(mecab, os.stat(mecab).st_mode | stat.S_IXUSR)
    return workdir, bindir


def _write_entry(workdir, name, title, body, status=None, superseded_by=None):
    lines = ["---", f'title: "{title}"']
    if status is not None:
        lines.append(f"status: {status}")
    if superseded_by is not None:
        lines.append(f"superseded_by: {superseded_by}")
    lines += ["---", "", body, ""]
    path = os.path.join(workdir, ".claude", "knowledge", "entries", "2026", name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _run_hook(workdir, bindir, search_status=None):
    """Run the hook in workdir; return (exit_code, additionalContext-or-'')."""
    env = dict(os.environ)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env.pop("CCMEMO_SEARCH_STATUS", None)
    if search_status is not None:
        env["CCMEMO_SEARCH_STATUS"] = search_status
    proc = subprocess.run(
        ["bash", HOOK],
        input=json.dumps({"prompt": "テスト prompt"}),
        capture_output=True,
        text=True,
        cwd=workdir,
        env=env,
    )
    if not proc.stdout.strip():
        return proc.returncode, ""
    payload = json.loads(proc.stdout)
    return proc.returncode, payload["hookSpecificOutput"]["additionalContext"]


FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        FAILURES.append(name)


def test_default_excludes_non_active():
    workdir, bindir = _make_workdir()
    try:
        _write_entry(workdir, "a-active.md", "Active entry", "alphaword", status="active")
        _write_entry(workdir, "b-superseded.md", "Old superseded entry", "alphaword",
                     status="superseded", superseded_by="2026/a-active.md")
        _write_entry(workdir, "c-deprecated.md", "Deprecated entry", "alphaword",
                     status="deprecated")
        code, ctx = _run_hook(workdir, bindir)
        check("default: hook exits 0", code == 0, f"code={code}")
        check("default: active surfaces", "Active entry" in ctx, ctx)
        check("default: superseded excluded", "b-superseded.md" not in ctx, ctx)
        check("default: deprecated excluded", "c-deprecated.md" not in ctx, ctx)
    finally:
        shutil.rmtree(workdir)


def test_missing_status_treated_as_active():
    workdir, bindir = _make_workdir()
    try:
        _write_entry(workdir, "nostatus.md", "Pre-status-era entry", "alphaword")
        code, ctx = _run_hook(workdir, bindir)
        check("no status line: still surfaces",
              code == 0 and "Pre-status-era entry" in ctx, ctx)
        check("no status line: no annotation", "[status:" not in ctx, ctx)
    finally:
        shutil.rmtree(workdir)


def test_widened_allowlist_annotates():
    workdir, bindir = _make_workdir()
    try:
        _write_entry(workdir, "a-active.md", "Active entry", "alphaword", status="active")
        _write_entry(workdir, "b-superseded.md", "Old superseded entry", "alphaword",
                     status="superseded", superseded_by="2026/a-active.md")
        code, ctx = _run_hook(workdir, bindir, search_status="active,superseded")
        check("widened: superseded surfaces", "Old superseded entry" in ctx, ctx)
        check("widened: annotated with status and superseded_by",
              "[status: superseded, superseded_by: 2026/a-active.md]" in ctx, ctx)
        active_lines = [l for l in ctx.splitlines() if "Active entry" in l]
        check("widened: active line not annotated",
              active_lines and all("[status:" not in l for l in active_lines),
              ctx)
    finally:
        shutil.rmtree(workdir)


def test_all_disables_filter():
    workdir, bindir = _make_workdir()
    try:
        _write_entry(workdir, "c-deprecated.md", "Deprecated entry", "alphaword",
                     status="deprecated")
        code, ctx = _run_hook(workdir, bindir, search_status="all")
        check("all: deprecated surfaces", "Deprecated entry" in ctx, ctx)
        check("all: annotated with bare status", "[status: deprecated]" in ctx, ctx)
    finally:
        shutil.rmtree(workdir)


def test_filtered_entries_do_not_consume_slots():
    workdir, bindir = _make_workdir()
    try:
        # The superseded entry matches both stub keywords, so it outranks the
        # five active entries (which match only one). With the filter on, all
        # five active entries must still surface (MAX_RESULTS=5): the dropped
        # top hit must not consume a result slot.
        _write_entry(workdir, "top-superseded.md", "Top-ranked superseded entry",
                     "alphaword betaword", status="superseded",
                     superseded_by="2026/active-1.md")
        for i in range(1, 6):
            _write_entry(workdir, f"active-{i}.md", f"Active entry {i}",
                         "alphaword", status="active")
        code, ctx = _run_hook(workdir, bindir)
        missing = [i for i in range(1, 6) if f"Active entry {i}" not in ctx]
        check("slots: all five active entries surface", not missing,
              f"missing={missing} ctx={ctx}")
        check("slots: superseded top hit excluded",
              "Top-ranked superseded entry" not in ctx, ctx)
    finally:
        shutil.rmtree(workdir)


def test_all_candidates_filtered_yields_no_output():
    workdir, bindir = _make_workdir()
    try:
        _write_entry(workdir, "b-superseded.md", "Old superseded entry", "alphaword",
                     status="superseded")
        code, ctx = _run_hook(workdir, bindir)
        check("all filtered: exits 0 with no injection", code == 0 and ctx == "",
              f"code={code} ctx={ctx}")
    finally:
        shutil.rmtree(workdir)


def test_body_status_line_does_not_leak():
    workdir, bindir = _make_workdir()
    try:
        # "status:" at line start in the BODY must not be read as frontmatter.
        _write_entry(workdir, "bodytrap.md", "Body mentions status",
                     "alphaword\nstatus: superseded\n(quoted example, not frontmatter)")
        code, ctx = _run_hook(workdir, bindir)
        check("body status line ignored", "Body mentions status" in ctx, ctx)
    finally:
        shutil.rmtree(workdir)


def main():
    for tool in ("bash", "jq", "rg"):
        if shutil.which(tool) is None:
            print(f"skip: required tool not on PATH: {tool}")
            return 0
    test_default_excludes_non_active()
    test_missing_status_treated_as_active()
    test_widened_allowlist_annotates()
    test_all_disables_filter()
    test_filtered_entries_do_not_consume_slots()
    test_all_candidates_filtered_yields_no_output()
    test_body_status_line_does_not_leak()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        return 1
    print("\nall tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
