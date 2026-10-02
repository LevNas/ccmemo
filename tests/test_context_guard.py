#!/usr/bin/env python3
"""Self-tests for hooks/stop_context_guard.py (mtime-based suppression).

Run: python3 tests/test_context_guard.py   (exit 0 = all pass)

Each test builds a throwaway project dir with a synthetic transcript and an
entries tree, then runs the hook as a subprocess with that cwd — the same way
the harness invokes it. All fixture content is synthetic.
"""

import hashlib
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


def run_hook(project, transcript, stop_hook_active=False, env_extra=None,
             session_id=None):
    env = dict(os.environ)
    env.pop("CCMEMO_CONTEXT_GUARD_THRESHOLD_KB", None)
    env.pop("CCMEMO_CONTEXT_GUARD_RECENT_WRITE_MIN", None)
    env.pop("XDG_CACHE_HOME", None)
    env["HOME"] = os.path.join(os.path.dirname(project), "home")  # never the real ~/.cache
    if env_extra:
        env.update(env_extra)
    payload = {"transcript_path": transcript, "stop_hook_active": stop_hook_active}
    if session_id is not None:
        payload["session_id"] = session_id
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


SESSION = "11111111-2222-3333-4444-555555555555"


def marker(cache, key=SESSION):
    digest = hashlib.sha256(key.encode()).hexdigest()[:32]
    return os.path.join(cache, "ccmemo", "context-guard", f"{digest}.nudge")


def long_session(base):
    return make_project(base, transcript_kb=400, entry_age_s=6 * 3600)


def test_one_nudge_per_window_across_turns():
    """A long session is nudged once per window, not at every turn."""
    with tempfile.TemporaryDirectory() as base:
        cache = os.path.join(base, "cache")
        project, transcript = long_session(base)
        env = {"XDG_CACHE_HOME": cache}
        first = run_hook(project, transcript, env_extra=env, session_id=SESSION)
        check("turn 1: block", first.get("decision") == "block", first)
        check("turn 1: nudge recorded", os.path.isfile(marker(cache)))
        again = run_hook(project, transcript, env_extra=env, session_id=SESSION,
                         stop_hook_active=True)
        check("turn 1 second stop (stop_hook_active): allow", again == {}, again)
        second = run_hook(project, transcript, env_extra=env, session_id=SESSION)
        check("turn 2 within window: allow", second == {}, second)
        other = run_hook(project, transcript, env_extra=env,
                         session_id="99999999-8888-7777-6666-555555555555")
        check("another session: own window, block",
              other.get("decision") == "block", other)


def test_nudge_window_expires():
    with tempfile.TemporaryDirectory() as base:
        cache = os.path.join(base, "cache")
        project, transcript = long_session(base)
        os.makedirs(os.path.dirname(marker(cache)))
        open(marker(cache), "w").close()
        past = time.time() - 3600
        os.utime(marker(cache), (past, past))
        out = run_hook(project, transcript, env_extra={"XDG_CACHE_HOME": cache},
                       session_id=SESSION)
        check("nudge older than the window: block", out.get("decision") == "block", out)
        check("expired marker refreshed",
              time.time() - os.path.getmtime(marker(cache)) < 60)


def test_marker_content_is_never_read():
    """A torn or corrupt marker cannot silence the guard: only its mtime counts."""
    with tempfile.TemporaryDirectory() as base:
        cache = os.path.join(base, "cache")
        project, transcript = long_session(base)
        os.makedirs(os.path.dirname(marker(cache)))
        with open(marker(cache), "w") as f:
            f.write('{"nudged_at": 1e999')
        past = time.time() - 3600
        os.utime(marker(cache), (past, past))
        out = run_hook(project, transcript, env_extra={"XDG_CACHE_HOME": cache},
                       session_id=SESSION)
        check("corrupt old marker: block", out.get("decision") == "block", out)


def test_future_marker_does_not_silence():
    with tempfile.TemporaryDirectory() as base:
        cache = os.path.join(base, "cache")
        project, transcript = long_session(base)
        os.makedirs(os.path.dirname(marker(cache)))
        open(marker(cache), "w").close()
        future = time.time() + 7 * 24 * 3600
        os.utime(marker(cache), (future, future))
        out = run_hook(project, transcript, env_extra={"XDG_CACHE_HOME": cache},
                       session_id=SESSION)
        check("marker dated in the future: block", out.get("decision") == "block", out)


def test_unwritable_cache_keeps_old_behavior():
    with tempfile.TemporaryDirectory() as base:
        blocker = os.path.join(base, "cache")
        with open(blocker, "w") as f:  # a file where the cache dir should be
            f.write("x")
        project, transcript = long_session(base)
        env = {"XDG_CACHE_HOME": blocker}
        first = run_hook(project, transcript, env_extra=env, session_id=SESSION)
        second = run_hook(project, transcript, env_extra=env, session_id=SESSION)
        check("unwritable cache: still blocks",
              first.get("decision") == "block" and second.get("decision") == "block",
              (first, second))


def test_any_session_id_stays_inside_the_cache():
    with tempfile.TemporaryDirectory() as base:
        cache = os.path.join(base, "cache")
        project, transcript = long_session(base)
        env = {"XDG_CACHE_HOME": cache}
        sid = "../../escape"
        first = run_hook(project, transcript, env_extra=env, session_id=sid)
        second = run_hook(project, transcript, env_extra=env, session_id=sid)
        written = [os.path.join(d, f) for d, _, fs in os.walk(base) for f in fs]
        outside = [p for p in written if p.endswith(".nudge")
                   and os.path.dirname(p) != os.path.dirname(marker(cache))]
        check("odd session id: block, then deduplicated",
              first.get("decision") == "block" and second == {}, (first, second))
        check("odd session id: marker only inside the cache dir",
              os.path.isfile(marker(cache, sid)) and not outside, outside)


def test_missing_session_id_falls_back_to_transcript():
    with tempfile.TemporaryDirectory() as base:
        cache = os.path.join(base, "cache")
        project, transcript = long_session(base)
        env = {"XDG_CACHE_HOME": cache}
        first = run_hook(project, transcript, env_extra=env)
        second = run_hook(project, transcript, env_extra=env)
        check("no session id: one nudge per transcript",
              first.get("decision") == "block" and second == {}, (first, second))


def test_default_cache_is_under_home():
    with tempfile.TemporaryDirectory() as base:
        project, transcript = long_session(base)
        out = run_hook(project, transcript, session_id=SESSION)
        home_cache = os.path.join(base, "home", ".cache")
        check("XDG_CACHE_HOME unset: marker under ~/.cache",
              out.get("decision") == "block" and os.path.isfile(marker(home_cache)), out)


def test_old_markers_pruned_one_by_one():
    with tempfile.TemporaryDirectory() as base:
        cache = os.path.join(base, "cache")
        project, transcript = long_session(base)
        directory = os.path.dirname(marker(cache))
        os.makedirs(os.path.join(directory, "sub.nudge"))  # not a file: skipped
        old = time.time() - 8 * 24 * 3600
        stale = [os.path.join(directory, n) for n in ("a.nudge", "b.json")]
        for p in stale:
            open(p, "w").close()
            os.utime(p, (old, old))
        run_hook(project, transcript, env_extra={"XDG_CACHE_HOME": cache}, session_id=SESSION)
        check("stale markers pruned past a directory entry",
              not any(os.path.exists(p) for p in stale), os.listdir(directory))


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
