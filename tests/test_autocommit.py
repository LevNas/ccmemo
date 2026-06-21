#!/usr/bin/env python3
"""Dependency-free self-tests for hooks/lib/autocommit (phase 2 safety-net commit).

Run: python3 tests/test_autocommit.py   (exit 0 = all pass)

Each test builds a throwaway git repo in a tempdir and exercises one gate /
behaviour of autocommit.run(). All "leaky" values below are SYNTHETIC (e.g. a
fake /home/<name>/ path) — never real secrets, per secrets-management.md.
"""

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib import autocommit  # noqa: E402


def _git(repo, *args, input_text=None, check=True):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        input=input_text,
        check=check,
    )


def _make_repo():
    repo = tempfile.mkdtemp(prefix="ccmemo-ac-")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _seed(repo):
    """Make an initial commit so HEAD exists."""
    _write(repo, "README.md", "root")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")


def _write(repo, rel, text):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


def _subjects(repo):
    out = _git(repo, "log", "--pretty=%s", check=False).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _head_message(repo):
    return _git(repo, "log", "-1", "--pretty=%B", check=False).stdout


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_disabled_is_noop():
    repo = _make_repo()
    try:
        _write(repo, ".claude/knowledge/entries/x.md", "hello")
        with _env(CCMEMO_AUTOCOMMIT=None):
            res = autocommit.run(repo, "SessionEnd")
        assert res.status == "disabled", res
        assert _subjects(repo) == [], _subjects(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_commits_knowledge_and_tasks():
    repo = _make_repo()
    try:
        _seed(repo)
        _write(repo, ".claude/knowledge/entries/foo.md", "a clean knowledge body")
        _write(repo, ".claude/tasks/mytask/todo.md", "- [ ] a")
        with _env(CCMEMO_AUTOCOMMIT="1", CCMEMO_AUTOCOMMIT_ON_LEAK=None):
            res = autocommit.run(repo, "SessionEnd")
        assert res.status == "committed", res
        msg = _head_message(repo)
        assert msg.startswith(
            "chore(ccmemo): checkpoint knowledge/tasks (2 files, SessionEnd)"
        ), msg
        assert "entries/foo.md" in msg and "mytask/todo.md" in msg, msg
        for bad in ("Co-Authored-By", "Generated with", "claude.ai/code", "Claude-Session"):
            assert bad not in msg, f"AI-attribution trailer leaked: {bad}"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_ignores_non_target_files():
    repo = _make_repo()
    try:
        _seed(repo)
        _write(repo, "README.md", "root changed")  # non-target edit
        _write(repo, ".claude/knowledge/entries/foo.md", "body")
        with _env(CCMEMO_AUTOCOMMIT="1", CCMEMO_AUTOCOMMIT_ON_LEAK=None):
            res = autocommit.run(repo, "SessionEnd")
        assert res.status == "committed", res
        assert res.files == [".claude/knowledge/entries/foo.md"], res.files
        # README.md change must still be pending (never swept in).
        st = _git(repo, "status", "--porcelain", "--", "README.md", check=False).stdout
        assert st.strip() != "", "non-target change must NOT be committed"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_clean_is_noop():
    repo = _make_repo()
    try:
        _seed(repo)
        with _env(CCMEMO_AUTOCOMMIT="1"):
            res = autocommit.run(repo, "SessionEnd")
        assert res.status == "clean", res
        assert _subjects(repo) == ["init"], _subjects(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_leak_blocks_by_default():
    repo = _make_repo()
    try:
        _seed(repo)
        _write(repo, ".claude/knowledge/entries/leak.md", "path /home/realuser/x/file")
        with _env(CCMEMO_AUTOCOMMIT="1", CCMEMO_AUTOCOMMIT_ON_LEAK=None):
            res = autocommit.run(repo, "SessionEnd")
        assert res.status == "blocked", res
        assert any(f.kind == "home-path" for _, f in res.findings), res.findings
        assert _subjects(repo) == ["init"], "must not commit when blocked"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_leak_warn_commits():
    repo = _make_repo()
    try:
        _seed(repo)
        _write(repo, ".claude/knowledge/entries/leak.md", "path /home/realuser/x/file")
        with _env(CCMEMO_AUTOCOMMIT="1", CCMEMO_AUTOCOMMIT_ON_LEAK="warn"):
            res = autocommit.run(repo, "SessionEnd")
        assert res.status == "committed", res
        assert res.findings, "warn mode should still report findings"
        assert _subjects(repo)[0].startswith("chore(ccmemo):"), _subjects(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_skips_during_merge():
    repo = _make_repo()
    try:
        _seed(repo)
        _write(repo, ".claude/knowledge/entries/foo.md", "body")
        # Simulate an in-progress merge without a real conflict.
        with open(os.path.join(repo, ".git", "MERGE_HEAD"), "w", encoding="utf-8") as f:
            f.write("0" * 40 + "\n")
        with _env(CCMEMO_AUTOCOMMIT="1"):
            res = autocommit.run(repo, "SessionEnd")
        assert res.status == "skipped", res
        assert _subjects(repo) == ["init"], "must not commit mid-merge"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


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
