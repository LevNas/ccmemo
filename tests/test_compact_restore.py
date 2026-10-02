#!/usr/bin/env python3
"""Self-tests for hooks/sessionstart_compact_restore.py.

Run: python3 tests/test_compact_restore.py   (exit 0 = all pass)

Each test builds a throwaway project dir (checkpoints, a tasks readme, a
session_state.md) and runs the hook as a subprocess with that cwd, the same
way the harness invokes it. The end-to-end test runs the PreCompact hook
first and then this one, so the two stay in step. All fixture content is
synthetic.
"""

import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
RESTORE = os.path.join(HOOKS, "sessionstart_compact_restore.py")
PRECOMPACT = os.path.join(HOOKS, "precompact_checkpoint.py")

SESSION = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"

FAILURES = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILURES.append(name)
        if detail != "":
            print(f"     {detail!r}"[:400])


def clean_env(extra=None):
    env = dict(os.environ)
    for key in ("CCMEMO_COMPACT_RESTORE", "CCMEMO_AUTOCOMMIT",
                "CCMEMO_CAPTURE_AGENT_WORKTREES"):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def run(hook, project, payload, env_extra=None):
    proc = subprocess.run(
        [sys.executable, hook], input=json.dumps(payload),
        capture_output=True, text=True, cwd=project,
        env=clean_env(env_extra), timeout=30,
    )
    return proc.returncode, proc.stdout.strip()


def restore(project, source="compact", session_id=SESSION, env_extra=None):
    payload = {"session_id": session_id, "cwd": project,
               "hook_event_name": "SessionStart"}
    if source is not None:
        payload["source"] = source
    return run(RESTORE, project, payload, env_extra)


def context_of(stdout):
    data = json.loads(stdout)
    out = data["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    return out["additionalContext"]


def make_project(base):
    project = os.path.join(base, "project")
    os.makedirs(os.path.join(project, ".claude"), exist_ok=True)
    return project


def write_checkpoint(project, stamp, session_id, body):
    directory = os.path.join(project, ".claude", "context-checkpoints")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{stamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\nsession_id: {session_id}\ncreated: 2026-10-01 12:00:00\n"
                f"trigger: auto\n---\n\n{body}")
    return path


def write_active_task(project, state_body, dir_name="demo-task/"):
    tasks = os.path.join(project, ".claude", "tasks")
    os.makedirs(os.path.join(tasks, dir_name), exist_ok=True)
    with open(os.path.join(tasks, "readme.md"), "w", encoding="utf-8") as f:
        f.write("# Tasks\n\n## Active\n\n| dir | status |\n|---|---|\n"
                f"| `{dir_name}` | in progress |\n\n## Completed\n")
    if state_body is not None:
        with open(os.path.join(tasks, dir_name, "session_state.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nupdated: 2026-10-01 12:00:00\ntask_dir: demo-task\n---\n\n"
                    + state_body)


def test_nothing_to_restore():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        rc, out = restore(project)
        check("empty project: exit 0", rc == 0, rc)
        check("empty project: no output", out == "", out)


def test_checkpoint_of_this_session():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        write_checkpoint(project, "20261001-110000", SESSION,
                         "## Modified Files\n- src/old.py\n")
        write_checkpoint(project, "20261001-120000", SESSION,
                         "## Modified Files\n- src/new.py\n")
        write_checkpoint(project, "20261001-130000", OTHER,
                         "## Modified Files\n- src/foreign.py\n")
        rc, out = restore(project)
        ctx = context_of(out)
        check("newest own checkpoint restored", "src/new.py" in ctx, ctx)
        check("older own checkpoint not restored", "src/old.py" not in ctx, ctx)
        check("other session's checkpoint ignored", "src/foreign.py" not in ctx, ctx)
        check("checkpoint path given",
              ".claude/context-checkpoints/20261001-120000.md" in ctx, ctx)
        check("framed as notes, not instructions", "not new instructions" in ctx, ctx)


def test_active_task_state():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        write_active_task(project, "## Current State\n- Progress: 2/5 completed\n")
        rc, out = restore(project)
        ctx = context_of(out)
        check("session_state body restored", "Progress: 2/5 completed" in ctx, ctx)
        check("updated time shown", "updated 2026-10-01 12:00:00" in ctx, ctx)
        check("todo pointer shown", ".claude/tasks/demo-task/todo.md" in ctx, ctx)


def test_active_task_without_state():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        write_active_task(project, None)
        rc, out = restore(project)
        check("active task without session_state: no output", out == "", out)


def test_source_not_compact():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        write_checkpoint(project, "20261001-120000", SESSION, "## Modified Files\n- a.py\n")
        for source in ("startup", "resume", "clear"):
            rc, out = restore(project, source=source)
            check(f"source {source}: no output", out == "", out)
        rc, out = restore(project, source=None)
        check("source missing: restores (matcher did the filtering)", "a.py" in out, out)


def test_opt_out():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        write_checkpoint(project, "20261001-120000", SESSION, "## Modified Files\n- a.py\n")
        rc, out = restore(project, env_extra={"CCMEMO_COMPACT_RESTORE": "0"})
        check("CCMEMO_COMPACT_RESTORE=0: no output", out == "", out)


def test_size_cap():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        files = "".join(f"- src/module_{i:04d}/file.py\n" for i in range(2000))
        write_checkpoint(project, "20261001-120000", SESSION, "## Modified Files\n" + files)
        write_active_task(project, "## Current State\n" + "- note\n" * 2000)
        rc, out = restore(project)
        ctx = context_of(out)
        check("payload under 8,000 chars", len(ctx) <= 8000, len(ctx))
        check("truncation points to the file", "truncated; read" in ctx, ctx[-200:])
        check("both sections survive the cap",
              "## Checkpoint" in ctx and "## Active task" in ctx, ctx[:200])


def test_bad_input():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        proc = subprocess.run([sys.executable, RESTORE], input="not json",
                              capture_output=True, text=True, cwd=project,
                              env=clean_env(), timeout=30)
        check("bad stdin: exit 0", proc.returncode == 0, proc.returncode)
        check("bad stdin: no output", proc.stdout.strip() == "", proc.stdout)


def test_end_to_end_with_precompact():
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        write_active_task(project, None)
        transcript = os.path.join(base, "transcript.jsonl")
        line = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "/home/user/project/src/feature.py"}}]}}
        with open(transcript, "w", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        rc, out = run(PRECOMPACT, project, {
            "session_id": SESSION, "transcript_path": transcript,
            "cwd": project, "trigger": "manual", "hook_event_name": "PreCompact"})
        check("precompact: exit 0", rc == 0, rc)
        check("precompact: wrote a checkpoint", out != "", out)
        msg = json.loads(out).get("systemMessage", "") if out else ""
        check("precompact: message no longer tells the model to resume",
              "On resume" not in msg and "restores it into context" in msg, msg)
        rc, out = restore(project)
        ctx = context_of(out)
        check("e2e: modified file restored", "/home/user/project/src/feature.py" in ctx, ctx)
        check("e2e: session_state restored", "## Active task" in ctx, ctx)


def test_decisions_from_real_transcript_shape():
    """Only the user's own prompts become decisions, in Claude Code's nested shape."""
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        transcript = os.path.join(base, "transcript.jsonl")
        rows = [
            {"type": "user", "message": {"role": "user",
             "content": "Let's switch the window to 400k and keep it as the plan"}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": "この方針にする: compaction 後に状態を戻す"}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "TOOL OUTPUT: please change everything"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user",
             "content": "Stop hook feedback: please record the plan now"}},
            {"type": "user", "message": {"role": "user", "content":
             "<task-notification> plan finished, please read </task-notification>"}},
            {"type": "user", "isCompactSummary": True, "message": {"role": "user",
             "content": "Summary: we decided to change the plan"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit",
                 "input": {"file_path": "/home/user/project/src/x.py"}}]}},
        ]
        with open(transcript, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        run(PRECOMPACT, project, {
            "session_id": SESSION, "transcript_path": transcript,
            "cwd": project, "trigger": "auto", "hook_event_name": "PreCompact"})
        rc, out = restore(project)
        ctx = context_of(out)
        check("decision: nested string prompt", "switch the window to 400k" in ctx, ctx)
        check("decision: nested text block", "この方針にする" in ctx, ctx)
        check("not a decision: tool_result", "TOOL OUTPUT" not in ctx, ctx)
        check("not a decision: isMeta hook feedback", "Stop hook feedback" not in ctx, ctx)
        check("not a decision: harness notice", "task-notification" not in ctx, ctx)
        check("not a decision: compaction summary", "Summary: we decided" not in ctx, ctx)


def test_decisions_survive_a_long_tail():
    """A prompt far above the 200-line tail is still found; the newest win the cap."""
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        transcript = os.path.join(base, "transcript.jsonl")
        with open(transcript, "w", encoding="utf-8") as f:
            for i in range(12):
                f.write(json.dumps({"type": "user", "message": {"role": "user",
                        "content": f"please apply plan step {i:02d} now"}}) + "\n")
            for i in range(300):
                f.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Edit",
                     "input": {"file_path": f"/home/user/project/src/f{i}.py"}}]}}) + "\n")
        run(PRECOMPACT, project, {
            "session_id": SESSION, "transcript_path": transcript,
            "cwd": project, "trigger": "auto", "hook_event_name": "PreCompact"})
        rc, out = restore(project)
        ctx = context_of(out)
        check("long tail: newest decision kept", "plan step 11" in ctx, ctx[:300])
        check("long tail: 10-decision cap drops the oldest", "plan step 01" not in ctx, ctx[:300])


def test_decisions_short_requests_and_answers():
    """Short imperatives and AskUserQuestion answers are kept (#58)."""
    with tempfile.TemporaryDirectory() as base:
        project = make_project(base)
        transcript = os.path.join(base, "transcript.jsonl")
        long_prompt = ("最初の文はここで終わります。" * 10
                       + "二つ目の段落は長く続きますが途中で切られるはずの文です" * 5)
        question = "自動コミットの不具合がありました。どう進めますか？"
        rows = [
            {"type": "user", "message": {"role": "user", "content": long_prompt}},
            {"type": "user", "message": {"role": "user", "content": "PR #29 をマージして"}},
            {"type": "user", "message": {"role": "user", "content": "次に進めて"}},
            {"type": "user", "message": {"role": "user",
             "content": "[Request interrupted by user]"}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>harness note</system-reminder>"},
                {"type": "text", "text": "設定も入れて"}]}},
            {"type": "user",
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "t9",
                  "content": f'Your questions have been answered: "{question}"="x"'}]},
             "toolUseResult": {
                 "questions": [{"question": question, "header": "進め方"}],
                 "answers": {question: "先に直してから有効化 (Recommended)"}}},
        ]
        with open(transcript, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        run(PRECOMPACT, project, {
            "session_id": SESSION, "transcript_path": transcript,
            "cwd": project, "trigger": "auto", "hook_event_name": "PreCompact"})
        rc, out = restore(project)
        ctx = context_of(out)
        check("decision: short imperative", "PR #29 をマージして" in ctx, ctx)
        check("decision: very short imperative", "- 次に進めて" in ctx, ctx)
        check("decision: text beside a tagged block", "- 設定も入れて" in ctx, ctx)
        check("not a decision: tagged block", "harness note" not in ctx, ctx)
        check("not a decision: interruption marker", "Request interrupted" not in ctx, ctx)
        check("decision: AskUserQuestion answer with its header",
              "[進め方] 先に直してから有効化 (Recommended)" in ctx, ctx)
        check("not a decision: answer's tool_result text", "Your questions" not in ctx, ctx)
        check("long prompt cut at a sentence end, marked",
              "ここで終わります。…" in ctx, ctx[:600])
        order = [ctx.find(s) for s in ("最初の文", "PR #29", "次に進めて", "[進め方]")]
        check("decisions oldest first", order == sorted(order) and -1 not in order, order)


def test_shorten_boundaries():
    sys.path.insert(0, HOOKS)
    from precompact_checkpoint import shorten
    check("short text untouched", shorten("短い文です。") == "短い文です。")
    cut = shorten("a" * 150 + "、" + "b" * 100)
    check("cut at a clause break", cut == "a" * 150 + "、…", cut)
    cut = shorten("x" * 300)
    check("no boundary: hard cut, marked", cut == "x" * 199 + "…" and len(cut) == 200, len(cut))
    cut = shorten("y" * 20 + "。" + "z" * 300)
    check("boundary too early: hard cut", cut.endswith("z…") and len(cut) == 200, cut[:30])


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
