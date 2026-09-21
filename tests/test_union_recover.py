#!/usr/bin/env python3
"""Dependency-free self-tests for `kb_graph.py union-recover` (issue #24).

Run: python3 tests/test_union_recover.py   (exit 0 = all pass)

Each test builds throwaway git repos in a tempdir (an "upstream" plus a
clone) and reproduces one divergence shape: a capture file conflicted by a
rebase, uncommitted local appends vs a fetched ref, and the edit-vs-edit
cases on knowledge entries that the append-only verification must refuse.
All fixture content is fictional.
"""

import os
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import kb_graph  # noqa: E402

KB_GRAPH = os.path.join(SCRIPTS, "kb_graph.py")

CAPTURE = ".claude/tasks/demo-task/context-20260701-090000-session.md"
CAPTURE_BASE = (
    "---\ncreated: 2026-07-01 09:00:00\nstatus: active\ntags: \"\"\n---\n\n"
    "- `09:01` **Write** `src/base.py`\n"
)
ENTRY = ".claude/knowledge/entries/2026/07/20260701-100000-alice-hub.md"
ENTRY_BASE = (
    "---\ntitle: Hub entry\nstatus: active\nupdated: 2026-07-01\n---\n\n"
    "Body line.\n\n## 関連\n\n"
    "- see: [Topic A](20260701-110000-alice-topic-a.md) — first neighbor\n"
)
SEE_B = "- see: [Topic B](20260702-090000-alice-topic-b.md) — added upstream\n"
SEE_C = "- see: [Topic C](20260703-090000-alice-topic-c.md) — added locally\n"


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=check)


def configure(repo):
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(repo, rel):
    with open(os.path.join(repo, rel), encoding="utf-8") as f:
        return f.read()


def make_pair(base, rel, text):
    """upstream repo with one committed file + a clone of it."""
    up = os.path.join(base, "upstream")
    os.makedirs(up)
    git(up, "init", "-q", "-b", "main")
    configure(up)
    write(up, rel, text)
    git(up, "add", "-A")
    git(up, "commit", "-qm", "base")
    clone = os.path.join(base, "clone")
    subprocess.run(["git", "clone", "-q", up, clone],
                   capture_output=True, text=True, check=True)
    configure(clone)
    return up, clone


def commit_upstream(up, clone, rel, text):
    write(up, rel, text)
    git(up, "commit", "-qam", "upstream change")
    git(clone, "fetch", "-q")


def run_cli(repo, *argv):
    return subprocess.run(
        [sys.executable, KB_GRAPH, "union-recover", *argv],
        capture_output=True, text=True, timeout=60, cwd=repo,
    )


def backups(repo):
    d = os.path.join(repo, ".git", "ccmemo-union-recover")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def in_tmp(fn):
    def wrapper():
        base = tempfile.mkdtemp(prefix="ccmemo-i24-")
        try:
            fn(base)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    wrapper.__name__ = fn.__name__
    return wrapper


# --- pure line logic ---------------------------------------------------------

def test_union_lines_keeps_everything_first_side_leading():
    merged = kb_graph.union_lines(["h", "b"], ["h", "b", "t1"], ["h", "b", "o1"])
    assert merged == ["h", "b", "t1", "o1"], merged


def test_union_lines_contained_addition_kept_once_partial_overlap_kept_twice():
    same = kb_graph.union_lines(["h"], ["h", "x"], ["h", "x"])
    assert same == ["h", "x"], same
    # one side already is an earlier union of both: re-running is a no-op
    for first, second in ((["h", "t"], ["h", "t", "o"]), (["h", "t", "o"], ["h", "o"])):
        again = kb_graph.union_lines(["h"], first, second)
        assert again == ["h", "t", "o"], again
    partial = kb_graph.union_lines(["h"], ["h", "x", "y"], ["h", "x", "z"])
    assert partial == ["h", "x", "y", "x", "z"], partial


def test_union_lines_mid_file_insertions_and_repeated_base_lines():
    base = ["a", "", "b", ""]
    merged = kb_graph.union_lines(base, ["a", "t", "", "b", ""],
                                  ["a", "", "b", "", "", "o"])
    assert merged == ["a", "t", "", "b", "", "", "o"], merged


def test_union_lines_refuses_deletion_and_rewrite():
    for bad in (["h"], ["h", "B"], ["b", "h"]):
        try:
            kb_graph.union_lines(["h", "b"], ["h", "b", "t"], bad, "up", "local")
        except kb_graph.UnionRefused as exc:
            assert "local is not append-only" in str(exc), exc
        else:
            raise AssertionError(f"{bad!r} was not refused")


# --- situation 1: conflicted by a rebase ------------------------------------

def make_rebase_conflict(base):
    up, clone = make_pair(base, CAPTURE, CAPTURE_BASE)
    theirs = CAPTURE_BASE + "- `10:00` **Write** `src/upstream.py`\n"
    ours = CAPTURE_BASE + "- `10:05` **Edit** `src/local.py` — `a` → `b`\n"
    write(clone, CAPTURE, ours)
    git(clone, "commit", "-qam", "local appends")
    commit_upstream(up, clone, CAPTURE, theirs)
    out = git(clone, "rebase", "origin/main", check=False)
    assert out.returncode != 0, "fixture: the rebase should conflict"
    return clone, theirs, ours


@in_tmp
def test_conflict_mode_unions_and_rebase_continues(base):
    clone, theirs, ours = make_rebase_conflict(base)
    conflicted = read(clone, CAPTURE)
    assert "<<<<<<<" in conflicted

    dry = run_cli(clone, CAPTURE, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "dry-run" in dry.stdout
    assert read(clone, CAPTURE) == conflicted, "--dry-run wrote the file"
    assert backups(clone) == [], "--dry-run left a backup"

    out = run_cli(clone, CAPTURE)
    assert out.returncode == 0, out.stderr
    result = read(clone, CAPTURE)
    assert "<<<<<<<" not in result and ">>>>>>>" not in result
    for source in (CAPTURE_BASE, theirs, ours):
        for line in source.splitlines():
            assert line in result.splitlines(), f"lost line: {line!r}"
    assert result.count("---\n") == 2, "frontmatter duplicated"
    # during a rebase stage 2 is upstream: its lines lead
    assert result.index("upstream.py") < result.index("local.py")

    saved = backups(clone)
    assert len(saved) == 1, saved
    assert read(clone, os.path.join(".git", "ccmemo-union-recover", saved[0])) == conflicted

    git(clone, "add", CAPTURE)
    cont = subprocess.run(
        ["git", "-C", clone, "-c", "core.editor=true", "rebase", "--continue"],
        capture_output=True, text=True)
    assert cont.returncode == 0, cont.stderr
    assert read(clone, CAPTURE) == result


@in_tmp
def test_conflict_mode_rejects_theirs_flag(base):
    clone, _theirs, _ours = make_rebase_conflict(base)
    before = read(clone, CAPTURE)
    out = run_cli(clone, CAPTURE, "--theirs", "origin/main")
    assert out.returncode != 0
    assert "nothing written" in out.stderr
    assert read(clone, CAPTURE) == before


# --- situation 2: uncommitted local changes vs a ref ------------------------

@in_tmp
def test_theirs_mode_unions_local_changes_then_syncs_cleanly(base):
    up, clone = make_pair(base, CAPTURE, CAPTURE_BASE)
    theirs = CAPTURE_BASE + "- `10:00` **Write** `src/upstream.py`\n"
    local = CAPTURE_BASE + "- `10:05` **Write** `src/local.py`\n"
    write(clone, CAPTURE, local)
    commit_upstream(up, clone, CAPTURE, theirs)
    pull = git(clone, "pull", "--ff-only", check=False)
    assert pull.returncode != 0, "fixture: pull should refuse to overwrite"

    dry = run_cli(clone, CAPTURE, "--theirs", "origin/main", "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert read(clone, CAPTURE) == local and backups(clone) == []

    out = run_cli(clone, CAPTURE, "--theirs", "origin/main")
    assert out.returncode == 0, out.stderr
    result = read(clone, CAPTURE)
    assert result == theirs + "- `10:05` **Write** `src/local.py`\n", result
    saved = backups(clone)
    assert len(saved) == 1
    assert read(clone, os.path.join(".git", "ccmemo-union-recover", saved[0])) == local

    # the advertised next step: commit, rebase — git re-reports the file as
    # conflicted ([T, O] vs [T]); re-running is a no-op union that resolves it
    git(clone, "commit", "-qam", "union")
    rebase = git(clone, "rebase", "origin/main", check=False)
    assert rebase.returncode != 0, "git is expected to stop on this file"
    again = run_cli(clone, CAPTURE)
    assert again.returncode == 0, again.stderr
    assert read(clone, CAPTURE) == result, "second run must reproduce the union"
    git(clone, "add", CAPTURE)
    cont = subprocess.run(
        ["git", "-C", clone, "-c", "core.editor=true", "rebase", "--continue"],
        capture_output=True, text=True)
    assert cont.returncode == 0, cont.stderr
    assert read(clone, CAPTURE) == result
    assert git(clone, "status", "--porcelain").stdout == ""


@in_tmp
def test_not_conflicted_without_theirs_is_refused(base):
    _up, clone = make_pair(base, CAPTURE, CAPTURE_BASE)
    out = run_cli(clone, CAPTURE)
    assert out.returncode != 0
    assert "--theirs" in out.stderr


@in_tmp
def test_file_absent_in_common_ancestor_is_refused(base):
    up, clone = make_pair(base, CAPTURE, CAPTURE_BASE)
    other = ".claude/tasks/demo-task/context-20260702-090000-session.md"
    write(clone, other, CAPTURE_BASE)
    write(up, other, CAPTURE_BASE + "- `11:00` **Write** `src/x.py`\n")
    git(up, "add", "-A")
    git(up, "commit", "-qm", "add other")
    git(clone, "fetch", "-q")
    out = run_cli(clone, other, "--theirs", "origin/main")
    assert out.returncode != 0
    assert "common ancestor" in out.stderr
    assert read(clone, other) == CAPTURE_BASE


# --- knowledge entries: see: appends union, edits are refused ---------------

@in_tmp
def test_entry_see_appends_union(base):
    up, clone = make_pair(base, ENTRY, ENTRY_BASE)
    write(clone, ENTRY, ENTRY_BASE + SEE_C)
    commit_upstream(up, clone, ENTRY, ENTRY_BASE + SEE_B)
    out = run_cli(clone, ENTRY, "--theirs", "origin/main")
    assert out.returncode == 0, out.stderr
    assert read(clone, ENTRY) == ENTRY_BASE + SEE_B + SEE_C
    assert "duplicate-link" in out.stdout


@in_tmp
def test_entry_frontmatter_rewrite_is_refused_untouched(base):
    up, clone = make_pair(base, ENTRY, ENTRY_BASE)
    local = ENTRY_BASE.replace("updated: 2026-07-01", "updated: 2026-07-09") + SEE_C
    write(clone, ENTRY, local)
    commit_upstream(up, clone, ENTRY, ENTRY_BASE + SEE_B)
    out = run_cli(clone, ENTRY, "--theirs", "origin/main")
    assert out.returncode != 0
    assert "local is not append-only" in out.stderr, out.stderr
    assert "updated: 2026-07-01" in out.stderr, "reason should name the lost line"
    assert read(clone, ENTRY) == local, "refusal must not write"
    assert backups(clone) == []


@in_tmp
def test_entry_status_rewrite_upstream_and_body_fix_are_refused(base):
    up, clone = make_pair(base, ENTRY, ENTRY_BASE)
    write(clone, ENTRY, ENTRY_BASE + SEE_C)
    commit_upstream(up, clone, ENTRY,
                    ENTRY_BASE.replace("status: active", "status: superseded") + SEE_B)
    out = run_cli(clone, ENTRY, "--theirs", "origin/main")
    assert out.returncode != 0
    assert "theirs (origin/main) is not append-only" in out.stderr, out.stderr
    assert read(clone, ENTRY) == ENTRY_BASE + SEE_C

    body_fix = ENTRY_BASE.replace("Body line.", "Body line, corrected.")
    write(clone, ENTRY, body_fix)
    out = run_cli(clone, ENTRY, "--theirs", "origin/main", "--dry-run")
    assert out.returncode != 0, "--dry-run must also fail on a refusal"
    assert read(clone, ENTRY) == body_fix


@in_tmp
def test_works_without_any_knowledge_base(base):
    # union-recover must not require entries under --root (captures-only repos)
    up, clone = make_pair(base, CAPTURE, CAPTURE_BASE)
    write(clone, CAPTURE, CAPTURE_BASE + "- `10:05` **Write** `src/local.py`\n")
    commit_upstream(up, clone, CAPTURE, CAPTURE_BASE + "- `10:00` **Write** `src/up.py`\n")
    out = run_cli(clone, CAPTURE, "--theirs", "origin/main", "--dry-run")
    assert out.returncode == 0, out.stderr


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
