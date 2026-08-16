#!/usr/bin/env python3
"""Self-tests for hooks/stop_context_guard.py (mtime-based suppression).

Run: python3 tests/test_context_guard.py   (exit 0 = all pass)

Each test builds a throwaway project dir with a synthetic transcript and an
entries tree, then runs the hook as a subprocess with that cwd — the same way
the harness invokes it. All fixture content is synthetic.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

HOOK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "hooks",
    "stop_context_guard.py",
)


def make_project(base, transcript_kb, entry_age_s=None, transcript_text=None):
    """Create a project dir + transcript; return (project_dir, transcript_path)."""
    project = os.path.join(base, "project")
    os.makedirs(os.path.join(project, ".claude", "knowledge", "entries", "2026"),
                exist_ok=True)
    if entry_age_s is not None:
        entry = os.path.join(project, ".claude", "knowledge", "entries", "2026",
                             "20260101-000000-alice-topic.md")
        with open(entry, "w", encoding="utf-8") as f:
            f.write("---\ntitle: T\n---\n\nbody\n")
        past = time.time() - entry_age_s
        os.utime(entry, (past, past))
    transcript = os.path.join(base, "transcript.jsonl")
    text = transcript_text or ("x" * 1024)
    with open(transcript, "w", encoding="utf-8") as f:
        while f.tell() < transcript_kb * 1024:
            f.write(text + "\n")
    return project, transcript


def run_hook(project, transcript, stop_hook_active=False, env_extra=None):
    env = dict(os.environ)
    env.pop("CCMEMO_CONTEXT_GUARD_THRESHOLD_KB", None)
    env.pop("CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN", None)
    if env_extra:
        env.update(env_extra)
    payload = {"transcript_path": transcript, "stop_hook_active": stop_hook_active}
    proc = subprocess.run(
        [sys.executable, HOOK], input=json.dumps(payload),
        capture_output=True, text=True, cwd=project, env=env, timeout=30,
    )
    return json.loads(proc.stdout)


FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        FAILURES.append(name)


def test_below_threshold_allows_stop():
    with tempfile.TemporaryDirectory() as base:
        project, transcript = make_project(base, transcript_kb=100)
        out = run_hook(project, transcript)
        check("below threshold: allow", out == {}, out)


def test_stop_hook_active_allows_stop():
    with tempfile.TemporaryDirectory() as base:
        project, transcript = make_project(base, transcript_kb=400)
        out = run_hook(project, transcript, stop_hook_active=True)
        check("stop_hook_active: allow", out == {}, out)


def test_big_transcript_and_stale_entries_block():
    with tempfile.TemporaryDirectory() as base:
        project, transcript = make_project(base, transcript_kb=400,
                                           entry_age_s=6 * 3600)
        out = run_hook(project, transcript)
        check("stale entries: block", out.get("decision") == "block", out)
        check("stale entries: reason has size", "KB" in out.get("reason", ""), out)


def test_no_entries_dir_content_blocks():
    with tempfile.TemporaryDirectory() as base:
        project, transcript = make_project(base, transcript_kb=400)
        out = run_hook(project, transcript)
        check("no entries yet: block", out.get("decision") == "block", out)


def test_fresh_entry_write_suppresses():
    with tempfile.TemporaryDirectory() as base:
        project, transcript = make_project(base, transcript_kb=400, entry_age_s=60)
        out = run_hook(project, transcript)
        check("fresh entry write: suppress", out == {}, out)


def test_path_mentions_in_transcript_do_not_suppress():
    # Regression: the old substring check was satisfied by mere path mentions
    # (the per-prompt auto-search injection alone contains entry paths).
    with tempfile.TemporaryDirectory() as base:
        mention = ('関連ナレッジ候補: ほげ '
                   '(.claude/knowledge/entries/2026/01/20260101-000000-a.md)')
        project, transcript = make_project(base, transcript_kb=400,
                                           entry_age_s=6 * 3600,
                                           transcript_text=mention)
        out = run_hook(project, transcript)
        check("path mentions alone: still block",
              out.get("decision") == "block", out)


def test_env_overrides():
    with tempfile.TemporaryDirectory() as base:
        project, transcript = make_project(base, transcript_kb=400,
                                           entry_age_s=6 * 3600)
        out = run_hook(project, transcript,
                       env_extra={"CCMEMO_CONTEXT_GUARD_THRESHOLD_KB": "1000"})
        check("raised threshold: allow", out == {}, out)
        # a 10-minute-old write is outside a 5-minute suppression window
        project2, transcript2 = make_project(
            os.path.join(base, "b"), transcript_kb=400, entry_age_s=600)
        out2 = run_hook(project2, transcript2,
                        env_extra={"CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN": "5"})
        check("shortened write window: block",
              out2.get("decision") == "block", out2)


def main():
    for t in sorted(k for k in globals() if k.startswith("test_")):
        globals()[t]()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
