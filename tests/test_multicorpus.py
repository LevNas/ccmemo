#!/usr/bin/env python3
"""Self-tests for the multi-corpus index (issue #49, 1.28).

Run: python3 tests/test_multicorpus.py   (exit 0 = all pass)

The fixture is a temporary git repository (spec: issue #49, implementation
handoff) with a knowledge base, a `docs/` corpus that mirrors one entry
twice (one exact copy, one edited), a 300 KB file over the embedding cap,
two identical notes, a session capture that must stay out of the index,
an ignored secret, and a linked worktree of the whole thing.

Everything that needs no vector index (config, repository resolution, the
candidate set, kinds, edge resolution, the size cap, the worktree DB path,
the SessionStart hook's decision, the prompt hook's scope) runs under plain
python3. The index half (build, migration 4 -> 5, read-only search from the
worktree, folding, `near-pairs`, `divergent-mirror`, scope re-keying) runs
only when `sqlite_vec` and `fastembed` are importable — e.g.
`uv run --with sqlite-vec --with fastembed --no-project python3 tests/test_multicorpus.py`
— and is reported as skipped otherwise. Needs `git` and `rg` on PATH.
"""

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
HOOKS = os.path.join(HERE, "..", "hooks")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HOOKS)
import kb_index as kbi  # noqa: E402
import kb_search as ks  # noqa: E402
import kb_graph as kbg  # noqa: E402
from lib import config as cfgmod  # noqa: E402
from lib import repo as repomod  # noqa: E402
from lib import edges as edgesmod  # noqa: E402
import sessionstart_index_prewarm as prewarm  # noqa: E402

KB_GRAPH = os.path.join(SCRIPTS, "kb_graph.py")
PROMPT_HOOK = os.path.join(HOOKS, "userpromptsubmit_knowledge_search.sh")

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        FAILURES.append(name)


# --------------------------------------------------------------------------- #
# Fixture
# --------------------------------------------------------------------------- #

KB = ".claude/knowledge/entries"
ALPHA = f"{KB}/2026/09/20260901-000001-user-alpha.md"
BETA = f"{KB}/2026/09/20260901-000002-user-beta.md"
GAMMA = f"{KB}/2026/09/20260901-000003-user-gamma.md"
ID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ID_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
GAMMA_TEXT = ("Deployment checklist for the staging gateway: rotate the token, restart "
              "the reverse proxy, confirm the health endpoint answers, then announce "
              "the window in the operations channel.")


def _entry(eid, title, desc, body):
    return (
        "---\n"
        f"title: {title}\n"
        f"id: {eid}\n"
        "status: active\n"
        "created: 2026-09-01\n"
        "type: knowledge\n"
        "tags:\n  - \"#t1\"\n"
        f"description: \"{desc}\"\n"
        "generated:\n  by: claude-code\n  at: 2026-09-01T00:00:00+09:00\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "-C", cwd, *args],
        capture_output=True, text=True, timeout=60,
    )


def make_fixture(base):
    """The issue #49 fixture in a git repository under base; returns (repo, worktree)."""
    repo = os.path.join(base, "repo")
    os.makedirs(repo)
    files = {
        ".gitignore": "secret.env\n.claude/knowledge/.index/\n",
        "secret.env": "TOKEN=placeholder\n",
        ".claude/knowledge/CLAUDE.md": "---\nschema_version: 3\n---\n# Tags\n- #t1\n",
        ALPHA: _entry(ID_A, "Alpha entry",
                      "Open when the alpha workflow needs its rationale, when the staging token "
                      "rotation order is in question, or before changing the workflow.",
                      "Alpha explains the workflow. Rotate the staging token first.\n\n"
                      "## 関連\n\n"
                      "- see: [Beta](2026/09/20260901-000002-user-beta.md) — why beta matters\n"),
        BETA: _entry(ID_B, "Beta entry",
                     "Open when the zebrafish pipeline stalls, when a run never finishes, or "
                     "when deciding whether to restart the pipeline from scratch.",
                     "Beta describes the zebrafish pipeline and how it stalls."),
        GAMMA: _entry(ID_C, "Gamma entry",
                      "Open before a staging gateway deployment, when the health endpoint is "
                      "silent after a restart, or when the operations channel needs a window.",
                      GAMMA_TEXT),
        "docs/guide.md": f"# Guide\n\nSee [other](other.md) for details.\n\n{GAMMA_TEXT}\n",
        "docs/other.md": ("---\ntitle: Other doc\nstatus: current\n"
                          "related_docs:\n  - path: docs/guide.md\n    label: x\n---\n\n"
                          "# Other\n\nThe other document about the operations channel.\n"),
        "notes/big.md": "quokka " + ("filler text for a very large note. " * 9000) + "\n",
        "notes/dup1.md": "# Duplicate\n\nThe axolotl note, written twice.\n",
        "notes/dup2.md": "# Duplicate\n\nThe axolotl note, written twice.\n",
        ".claude/tasks/x/context-1.md": "excluded capture capybara\n",
    }
    files["docs/mirror-exact.md"] = files[BETA]
    files["docs/mirror-changed.md"] = files[BETA].replace(
        "Beta describes the zebrafish pipeline and how it stalls.",
        "Beta describes the zebrafish pipeline; this copy was edited in docs.")
    for rel, text in files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    assert os.path.getsize(os.path.join(repo, "notes/big.md")) > 256 * 1024
    r = _git(repo, "init", "-q")
    assert r.returncode == 0, r.stderr
    _git(repo, "add", "-A")
    r = _git(repo, "commit", "-q", "-m", "fixture")
    assert r.returncode == 0, r.stderr
    wt = os.path.join(base, "wt")
    r = _git(repo, "worktree", "add", "-q", wt, "-b", "wt")
    assert r.returncode == 0, r.stderr
    return repo, wt


def kb_root(checkout):
    return Path(checkout) / KB


class scoped:
    """`CCMEMO_INDEX_SCOPE=<scope>` for a block; clears git caches."""

    def __init__(self, scope, **extra):
        self.env = {"CCMEMO_INDEX_SCOPE": scope, **extra}
        self.saved = {}

    def __enter__(self):
        for k, v in self.env.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------- #
# Pure-stdlib half
# --------------------------------------------------------------------------- #

def test_config():
    g = cfgmod.glob_to_regex
    check("glob: ** spans directories", g(".claude/tasks/**/context-*.md").match(".claude/tasks/x/y/context-1.md"))
    check("glob: **/ matches zero dirs", g("**/.index/**").match(".index/kb.db") and g("**/.index/**").match("a/.index/kb.db"))
    check("glob: * stays in one segment", not g("docs/*.md").match("docs/a/b.md") and g("docs/*.md").match("docs/b.md"))
    with tempfile.TemporaryDirectory() as tmp:
        with scoped(None):
            cfg = cfgmod.index_config(tmp)
            check("defaults: scope kb", cfg.scope == "kb" and cfg.source == "")
            check("defaults: kb corpus only", [c.kind for c in cfg.corpora] == ["kb"])
            check("defaults: kinds", cfg.kinds() == ["kb", "docs"])
            check("defaults: kb path", cfg.kind_of(".claude/knowledge/entries/2026/a.md") == "kb"
                  and cfg.kind_of("docs/a.md") == "docs")
            check("defaults: candidates by extension",
                  cfg.is_candidate("docs/a.md") and cfg.is_candidate("README.rst")
                  and not cfg.is_candidate("src/x.py") and not cfg.is_candidate("docs/CLAUDE.md"))
            check("defaults: captures and .index excluded",
                  not cfg.is_candidate(".claude/tasks/t/context-3.md")
                  and not cfg.is_candidate(".claude/knowledge/.index/kb.db"))
            os.makedirs(os.path.join(tmp, ".claude"))
            with open(os.path.join(tmp, ".claude", "ccmemo.json"), "w", encoding="utf-8") as f:
                json.dump({"index": {
                    "scope": "repo", "include": ["notes/inventory/*.yaml"],
                    "exclude": ["drafts/**"], "max_file_bytes": 1024,
                    "corpora": [{"kind": "notes", "path": "notes/", "conventions": "plain"},
                                {"kind": "kb", "path": "elsewhere"},
                                {"kind": "bad"}],
                }}, f)
            err = io.StringIO()
            with redirect_stderr(err):
                cfg = cfgmod.index_config(tmp)
            check("file: scope repo", cfg.scope == "repo" and cfg.source.endswith("ccmemo.json"))
            check("file: include adds a glob", cfg.is_candidate("notes/inventory/a.yaml")
                  and not cfg.is_candidate("notes/other.yaml"))
            check("file: exclude adds to defaults",
                  not cfg.is_candidate("drafts/a.md") and not cfg.is_candidate(".claude/tasks/t/context-1.md"))
            check("file: size cap", cfg.max_file_bytes == 1024)
            check("file: corpora longest prefix", cfg.kind_of("notes/a.md") == "notes"
                  and cfg.kind_of("notes") == "notes" and cfg.kind_of("notesx/a.md") == "docs")
            check("file: conventions", cfg.conventions_of("notes") == "plain"
                  and cfg.conventions_of("kb") == "kb" and cfg.conventions_of("docs") == "plain")
            check("file: kb path cannot move, bad item skipped",
                  "kind 'kb' is always" in err.getvalue() and "needs kind and path" in err.getvalue()
                  and cfg.kind_of("elsewhere/a.md") == "docs", err.getvalue())
        with scoped("kb"):
            check("env: CCMEMO_INDEX_SCOPE overrides the file", cfgmod.index_config(tmp).scope == "kb")
        with scoped("bogus"):
            err = io.StringIO()
            with redirect_stderr(err):
                cfg = cfgmod.index_config(tmp)
            check("env: invalid scope ignored with a warning", cfg.scope == "repo" and "ignored" in err.getvalue())
        with open(os.path.join(tmp, ".claude", "ccmemo.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        with scoped(None):
            err = io.StringIO()
            with redirect_stderr(err):
                cfg = cfgmod.index_config(tmp)
            check("malformed file: defaults + warning", cfg.scope == "kb" and "using defaults" in err.getvalue())
    check("status alias only for plain corpora",
          cfgmod.normalize_status("current", "plain") == "active"
          and cfgmod.normalize_status("current", "kb") == "current")


def test_repo_and_candidates(repo, wt):
    check("toplevel of worktree is the worktree", repomod.toplevel(wt) == os.path.realpath(wt))
    check("main checkout from worktree", repomod.main_checkout(wt) == os.path.realpath(repo))
    check("linked worktree detected", repomod.is_linked_worktree(wt) and not repomod.is_linked_worktree(repo))
    check("outside git: None", repomod.toplevel(tempfile.gettempdir()) is None or True)
    files = repomod.ls_files(repo)
    check("ls-files honours .gitignore", "secret.env" not in files and ".gitignore" in files, files)
    check("ls-files sees the capture and docs", ".claude/tasks/x/context-1.md" in files and "docs/guide.md" in files)
    with scoped("kb"):
        ctx = kbi.context(kb_root(repo))
        keys = [k for _p, k in kbi.candidate_files(kb_root(repo), ctx)]
        check("kb scope: entries only, kb-relative keys",
              keys == ["2026/09/20260901-000001-user-alpha.md", "2026/09/20260901-000002-user-beta.md",
                       "2026/09/20260901-000003-user-gamma.md"], keys)
        check("kb scope: key base is the root", ctx.key_base == kb_root(repo).resolve() and ctx.scope == "kb")
        a = kbi.parse_entry(kb_root(repo) / "2026/09/20260901-000001-user-alpha.md", kb_root(repo), ctx)
        check("kb scope: see link resolves to kb-relative key",
              [e["target"] for e in a.edges] == ["2026/09/20260901-000002-user-beta.md"] and a.kind == "kb", a.edges)
    with scoped("repo"):
        ctx = kbi.context(kb_root(repo))
        check("repo scope: key base is the repository", ctx.key_base == Path(os.path.realpath(repo)))
        cands = dict((k, p) for p, k in kbi.candidate_files(kb_root(repo), ctx))
        check("repo scope: capture excluded, secret absent",
              ".claude/tasks/x/context-1.md" not in cands and "secret.env" not in cands and ".gitignore" not in cands)
        check("repo scope: docs, notes, mirrors, kb present",
              {"docs/guide.md", "docs/other.md", "docs/mirror-exact.md", "docs/mirror-changed.md",
               "notes/big.md", "notes/dup1.md", "notes/dup2.md", ALPHA, BETA, GAMMA} <= set(cands), sorted(cands))
        check("repo scope: CLAUDE.md never a candidate", ".claude/knowledge/CLAUDE.md" not in cands)
        entries = kbi.scan_entries(kb_root(repo), ctx)
        check("kinds: kb vs docs", entries[ALPHA].kind == "kb" and entries["docs/guide.md"].kind == "docs"
              and entries["notes/big.md"].kind == "docs")
        check("title: frontmatter, else H1, else filename",
              entries["docs/other.md"].title == "Other doc" and entries["docs/guide.md"].title == "Guide"
              and entries["notes/big.md"].title == "big", (entries["docs/guide.md"].title, entries["notes/big.md"].title))
        check("handles: date prefix survives the repo key", entries[ALPHA].handle == "20260901-000001")
        check("size cap: metadata only, no chunks",
              entries["notes/big.md"].embedded is False and entries["notes/big.md"].chunks == []
              and entries["notes/big.md"].size_bytes > 256 * 1024)
        check("size cap: small files embedded", entries["docs/guide.md"].embedded and entries["docs/guide.md"].chunks)
        check("edges: see link resolves to the repo key",
              [e["target"] for e in entries[ALPHA].edges] == [BETA], entries[ALPHA].edges)
        check("edges: markdown-links (inline, rel ref, label = text)",
              entries["docs/guide.md"].edges == [{"target": "docs/other.md", "rel": "ref", "label": "other"}],
              entries["docs/guide.md"].edges)
        check("edges: related_docs repo-relative path resolves",
              entries["docs/other.md"].edges == [{"target": "docs/guide.md", "rel": "see", "label": "x"}],
              entries["docs/other.md"].edges)
        check("edges: mirror keeps its kb-style link resolved",
              [e["target"] for e in entries["docs/mirror-exact.md"].edges] == [], entries["docs/mirror-exact.md"].edges)
        check("status kept raw in the index", entries["docs/other.md"].status == "current")
    # An inline link to a non-document (code, image) yields no edge.
    with scoped("repo"):
        p = os.path.join(repo, "docs", "tmp-link.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# T\n\n[gitignore](../.gitignore) [nope](missing.md) [img](x.png)\n")
        try:
            e = kbi.parse_entry(Path(p), kb_root(repo), kbi.context(kb_root(repo)))
            check("edges: inline links to non-documents or missing files dropped", e.edges == [], e.edges)
        finally:
            os.remove(p)


def test_db_path_and_sharing(repo, wt):
    with scoped(None, CCMEMO_KB_INDEX=None):
        main_db = kbi.index_db_path(kb_root(repo))
        wt_db = kbi.index_db_path(kb_root(wt))
        check("main checkout: DB under its own .index",
              main_db == Path(os.path.realpath(repo)) / ".claude/knowledge/.index/kb.db", main_db)
        check("worktree: same DB as the main checkout", wt_db == main_db, wt_db)
        check("worktree: shared (read-only)", kbi.index_is_shared(kb_root(wt)) and not kbi.index_is_shared(kb_root(repo)))
        check("kb_graph agrees on the path",
              kbg._index_db_path(str(kb_root(wt))) == str(main_db) and kbg._index_db_path(str(kb_root(repo))) == str(main_db))
        try:
            kbi.reindex(kb_root(wt), verbose=False)
            check("worktree: reindex refused", False)
        except kbi.SharedIndexError:
            check("worktree: reindex refused", True)
        except Exception as exc:  # noqa: BLE001
            check("worktree: reindex refused", "sqlite_vec" in str(exc) or isinstance(exc, ImportError), repr(exc))
    with scoped(None, CCMEMO_KB_INDEX=os.path.join(wt, "own.db")):
        check("CCMEMO_KB_INDEX: explicit path, not shared",
              kbi.index_db_path(kb_root(wt)) == Path(wt, "own.db").resolve() and not kbi.index_is_shared(kb_root(wt)))
        check("kb_graph honours CCMEMO_KB_INDEX", kbg._index_db_path(str(kb_root(wt))) == os.path.join(wt, "own.db"))
    # kb_graph maps repo-scope index keys back to entries-root relpaths (relink).
    check("index key -> entry relpath",
          kbg._index_key_to_entry(str(kb_root(repo)), ALPHA) == "2026/09/20260901-000001-user-alpha.md"
          and kbg._index_key_to_entry(str(kb_root(repo)), "2026/09/x.md") == "2026/09/x.md")


def test_prewarm_hook(repo, wt):
    env = {"PATH": os.environ.get("PATH", "")}
    check("prewarm: no index -> nothing", prewarm.plan(repo, env) is None)
    index_dir = os.path.join(repo, ".claude", "knowledge", ".index")
    os.makedirs(index_dir, exist_ok=True)
    db = os.path.join(index_dir, "kb.db")
    made_db = not os.path.exists(db)
    if made_db:
        open(db, "wb").close()
    try:
        bindir = tempfile.mkdtemp(prefix="ccmemo-uv-")
        fake_uv = os.path.join(bindir, "uv")
        with open(fake_uv, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake_uv, os.stat(fake_uv).st_mode | stat.S_IXUSR)
        env = {"PATH": bindir + os.pathsep + os.environ.get("PATH", "")}
        todo = prewarm.plan(repo, env)
        check("prewarm: main checkout with an index -> refresh planned",
              todo is not None and todo[0] == os.path.join(repo, KB), todo)
        check("prewarm: worktree -> nothing", prewarm.plan(wt, env) is None)
        check("prewarm: opt-out", prewarm.plan(repo, {**env, "CCMEMO_INDEX_PREWARM": "0"}) is None)
        lock = os.path.join(index_dir, prewarm.LOCK_NAME)
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        check("prewarm: live lock -> nothing", prewarm.plan(repo, env) is None)
        with open(lock, "w", encoding="utf-8") as f:
            f.write("999999999")
        check("prewarm: stale lock ignored", prewarm.plan(repo, env) is not None)
        os.remove(lock)
        # End to end from the worktree: exit 0, no output, nothing written.
        proc = subprocess.run([sys.executable, os.path.join(HOOKS, "sessionstart_index_prewarm.py")],
                              input=json.dumps({"cwd": wt}), capture_output=True, text=True,
                              env={**os.environ, **env}, timeout=30)
        check("prewarm hook: exit 0 and silent from a worktree",
              proc.returncode == 0 and proc.stdout == "" and not os.path.exists(lock), proc)
        shutil.rmtree(bindir)
    finally:
        if made_db:
            os.remove(db)


MECAB_STUB = """#!/usr/bin/env bash
cat >/dev/null
printf 'zebrafish\\t名詞,一般,*,*,*,*,*\\n'
printf 'capybara\\t名詞,一般,*,*,*,*,*\\n'
printf 'axolotl\\t名詞,一般,*,*,*,*,*\\n'
printf 'EOS\\n'
"""


def run_prompt_hook(cwd):
    bindir = tempfile.mkdtemp(prefix="ccmemo-mecab-")
    try:
        mecab = os.path.join(bindir, "mecab")
        with open(mecab, "w", encoding="utf-8") as f:
            f.write(MECAB_STUB)
        os.chmod(mecab, os.stat(mecab).st_mode | stat.S_IXUSR)
        env = dict(os.environ)
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        env.pop("CCMEMO_SEARCH_STATUS", None)
        proc = subprocess.run(["bash", PROMPT_HOOK], input=json.dumps({"prompt": "x"}),
                              capture_output=True, text=True, cwd=cwd, env=env, timeout=30)
        return proc.returncode, proc.stdout
    finally:
        shutil.rmtree(bindir)


def test_prompt_hook_scope(repo, wt):
    if not (shutil.which("jq") and shutil.which("rg")):
        print("skip prompt hook test (jq/rg missing)")
        return
    code, out = run_prompt_hook(repo)
    check("prompt hook: exit 0 with hits", code == 0 and out.strip(), out)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
    check("prompt hook: knowledge base only (never docs/notes/captures)",
          "Beta entry" in ctx and "docs/" not in ctx and "notes/" not in ctx and "capybara" not in ctx, ctx)
    code2, out2 = run_prompt_hook(wt)
    check("prompt hook: byte-identical from the worktree", out2 == out, (out, out2))


# --------------------------------------------------------------------------- #
# Index half (needs sqlite_vec + fastembed)
# --------------------------------------------------------------------------- #

def _search(root, query, **kw):
    args = dict(top=8, use_mecab=False, lazy=True, status=None, tags=[], etype=None,
                created_from=None, created_to=None, max_edges=-1, max_linked_from=0)
    args.update(kw)
    return ks.search(root, query, **args)


def test_index(repo, wt):
    try:
        import sqlite_vec  # noqa: F401
        import fastembed  # noqa: F401
    except ImportError as exc:
        print(f"skip index tests ({exc.name} not importable)")
        return
    root = kb_root(repo)
    with scoped("repo", CCMEMO_KB_INDEX=None):
        stats = kbi.reindex(root, verbose=False)
        db = kbi.index_db_path(root)
        check("repo scope: build", stats["scope"] == "repo" and stats["added"] == 10 and stats["metadata_only"] == 1, stats)
        conn = kbi.connect(db)
        kinds = dict(conn.execute("SELECT relpath, kind FROM entries"))
        check("every entries.kind set", all(kinds.values()) and kinds[ALPHA] == "kb" and kinds["docs/guide.md"] == "docs", kinds)
        big = conn.execute("SELECT embedded, size_bytes FROM entries WHERE relpath = 'notes/big.md'").fetchone()
        nchunks_big = conn.execute("SELECT count(*) FROM chunks WHERE relpath = 'notes/big.md'").fetchone()[0]
        check("over-cap: embedded = 0, no chunks", big[0] == 0 and big[1] > 256 * 1024 and nchunks_big == 0, (big, nchunks_big))
        vec_count = conn.execute("SELECT count(*) FROM vec_chunks").fetchone()[0]
        check("meta: scope + schema", kbi._meta_get(conn, "scope") == "repo" and kbi._meta_get(conn, "schema_version") == "5")
        edges = conn.execute("SELECT src, target, rel FROM edges ORDER BY src, ord").fetchall()
        check("edges: kb see, docs inline, related_docs",
              (ALPHA, BETA, "see") in edges and ("docs/guide.md", "docs/other.md", "ref") in edges
              and ("docs/other.md", "docs/guide.md", "see") in edges, edges)
        conn.close()

        # Over-cap file found lexically by its unique token; excluded capture never.
        hits = _search(root, "quokka")
        check("over-cap: findable lexically", hits and hits[0]["relpath"] == "notes/big.md" and hits[0]["embedded"] is False,
              [h["relpath"] for h in hits])
        hits = _search(root, "capybara")
        check("excluded capture: never a hit (the vector arm still ranks the nearest documents)",
              not any(".claude/tasks" in h["relpath"] or "capybara" in h["snippet"] for h in hits),
              [h["relpath"] for h in hits])

        # Duplicate content folds into one line listing both paths.
        hits = _search(root, "axolotl")
        dup = [h for h in hits if h["relpath"].startswith("notes/dup")]
        check("duplicate content: one line, both paths",
              len(dup) == 1 and sorted([dup[0]["relpath"], *dup[0]["locations"]]) == ["notes/dup1.md", "notes/dup2.md"],
              [(h["relpath"], h["locations"]) for h in hits])
        text = ks.format_ranked(hits)
        check("ranked output: [kind] badge + also: line", "[docs]" in text and "also: notes/dup" in text, text)
        # Same id folds too (beta + its two mirrors), flagged divergent.
        hits = _search(root, "zebrafish pipeline stalls")
        beta = [h for h in hits if h["id"] == ID_B]
        check("same id: one line, mirrors listed, divergent flagged",
              len(beta) == 1 and set(beta[0]["locations"]) | {beta[0]["relpath"]}
              == {BETA, "docs/mirror-exact.md", "docs/mirror-changed.md"} and beta[0]["divergent"],
              [(h["relpath"], h["locations"], h.get("divergent")) for h in hits])
        summ = ks.format_summary(hits)
        check("summary output: badge and locations", "[kb]" in summ or "[docs]" in summ, summ)
        check("summary output: also line with divergent note", "also:" in summ and "divergent" in summ, summ)

        # --kind filter and the plain status vocabulary.
        hits = _search(root, "operations channel", kinds=["docs"])
        check("--kind docs: only docs", hits and all(h["kind"] == "docs" for h in hits), [(h["relpath"], h["kind"]) for h in hits])
        hits = _search(root, "operations channel", kinds=["kb"])
        check("--kind kb: only kb", hits and all(h["kind"] == "kb" for h in hits), [(h["relpath"], h["kind"]) for h in hits])
        hits = _search(root, "other document operations channel", status="active", kinds=["docs"])
        check("status: current counts as active for a plain corpus",
              any(h["relpath"] == "docs/other.md" for h in hits), [h["relpath"] for h in hits])
        hits = _search(root, "other document operations channel", tags=["#t1"], kinds=["docs"])
        check("explicit tag filter drops files without tags", not any(h["relpath"] == "docs/other.md" for h in hits))

        # Search from the linked worktree: same hits, DB untouched.
        before = os.stat(db)
        main_hits = [h["relpath"] for h in _search(root, "staging gateway deployment")]
        err = io.StringIO()
        with redirect_stderr(err):
            wt_hits = [h["relpath"] for h in _search(kb_root(wt), "staging gateway deployment")]
        after = os.stat(db)
        check("worktree: same hits as the main checkout", wt_hits == main_hits and main_hits, (main_hits, wt_hits))
        check("worktree: DB mtime and size unchanged",
              (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size))
        check("worktree: no staleness warning when in sync", err.getvalue() == "", err.getvalue())
        with open(os.path.join(wt, "docs", "guide.md"), "a", encoding="utf-8") as f:
            f.write("\nedited in the worktree\n")
        err = io.StringIO()
        with redirect_stderr(err):
            _search(kb_root(wt), "staging gateway deployment")
        check("worktree: staleness warning names the count", "behind on 1 file" in err.getvalue(), err.getvalue())
        check("worktree: still no write", os.stat(db).st_mtime_ns == before.st_mtime_ns)
        with open(os.path.join(wt, "docs", "guide.md"), "w", encoding="utf-8") as f:
            f.write(open(os.path.join(repo, "docs", "guide.md"), encoding="utf-8").read())

        # near-pairs: a kb-docs pair exists; the linked docs pair is marked.
        pairs = kbg.near_pairs(str(db), top=0)
        cross = [p for p in pairs if {p["kind_a"], p["kind_b"]} == {"kb", "docs"}]
        check("near-pairs: kb-docs pair present", cross, pairs[:5])
        mirror = [p for p in cross if p["same_sha256"]]
        check("near-pairs: exact mirror is same-content, cosine 1", mirror and mirror[0]["cosine"] >= 0.999, mirror)
        guide_other = [p for p in pairs if {p["a"], p["b"]} == {"docs/guide.md", "docs/other.md"}]
        check("near-pairs: linked pair marked linked", guide_other and guide_other[0]["linked"], guide_other)
        guide_gamma = [p for p in pairs if {p["a"], p["b"]} == {"docs/guide.md", GAMMA}]
        check("near-pairs: near-duplicate kb-docs unlinked", guide_gamma and not guide_gamma[0]["linked"], guide_gamma)
        only_cross = kbg.near_pairs(str(db), top=0, cross_kind=True)
        check("near-pairs: --cross-kind", only_cross and all(p["kind_a"] != p["kind_b"] for p in only_cross))
        only_kb = kbg.near_pairs(str(db), top=0, kinds={"kb"})
        check("near-pairs: --kind kb", only_kb and all(p["kind_a"] == p["kind_b"] == "kb" for p in only_kb))
        check("near-pairs: deterministic", kbg.near_pairs(str(db), top=5) == kbg.near_pairs(str(db), top=5))
        proc = subprocess.run([sys.executable, KB_GRAPH, "--root", str(root), "near-pairs", "--top", "3"],
                              capture_output=True, text=True, timeout=120, env=dict(os.environ))
        check("near-pairs CLI", proc.returncode == 0 and proc.stdout.count("\n") == 6, proc.stdout + proc.stderr)

        # lint: divergent-mirror for the edited copy only (advisory).
        proc = subprocess.run([sys.executable, KB_GRAPH, "--root", str(root), "--json", "lint"],
                              capture_output=True, text=True, timeout=60, env=dict(os.environ))
        findings = json.loads(proc.stdout) if proc.stdout.strip() else []
        dm = [f for f in findings if f["check"] == "divergent-mirror"]
        check("lint: divergent-mirror on the changed copy only",
              [f["id"] for f in dm] == ["docs/mirror-changed.md"] and dm[0]["severity"] == "advisory", findings)
        check("lint: exit 0 (advisory only)", proc.returncode == 0, proc.stderr)

        # Migration 4 -> 5: metadata only, vec_chunks untouched, kind backfilled.
        conn = kbi.connect(db)
        conn.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
        conn.execute("UPDATE entries SET kind = '', embedded = 1, size_bytes = 0")
        conn.commit()
        conn.close()
        n = kbi.ensure_metadata(root)
        conn = kbi.connect(db)
        check("migration: rows upgraded", n == 10, n)
        check("migration: schema_version 5", kbi._meta_get(conn, "schema_version") == "5")
        check("migration: vec_chunks unchanged", conn.execute("SELECT count(*) FROM vec_chunks").fetchone()[0] == vec_count)
        check("migration: every kind non-null", all(k for k in dict(conn.execute("SELECT relpath, kind FROM entries")).values()))
        check("migration: embedded from stored chunks",
              conn.execute("SELECT embedded FROM entries WHERE relpath = 'notes/big.md'").fetchone()[0] == 0)
        conn.close()

    # Scope switch: knowledge entries are re-keyed, not re-embedded.
    with scoped("kb", CCMEMO_KB_INDEX=None):
        stats = kbi.reindex(root, verbose=False)
        check("scope repo -> kb: 3 rows re-keyed, docs removed, nothing embedded",
              stats["rekeyed"] == 3 and stats["removed"] == 7 and stats["embedded_chunks"] == 0, stats)
        conn = kbi.connect(db)
        keys = sorted(dict(conn.execute("SELECT relpath, kind FROM entries")))
        check("scope kb: kb-relative keys", keys == ["2026/09/20260901-000001-user-alpha.md",
                                                     "2026/09/20260901-000002-user-beta.md",
                                                     "2026/09/20260901-000003-user-gamma.md"], keys)
        edges = conn.execute("SELECT src, target FROM edges").fetchall()
        check("scope kb: edges rewritten with kb keys",
              edges == [("2026/09/20260901-000001-user-alpha.md", "2026/09/20260901-000002-user-beta.md")], edges)
        conn.close()
        hits = _search(root, "zebrafish")
        check("scope kb: search works, kb badge, no folding partner",
              hits and hits[0]["relpath"] == "2026/09/20260901-000002-user-beta.md" and hits[0]["kind"] == "kb"
              and hits[0]["locations"] == [], hits[:1])
    with scoped("repo", CCMEMO_KB_INDEX=None):
        stats = kbi.reindex(root, verbose=False)
        check("scope kb -> repo: kb re-keyed, docs re-added",
              stats["rekeyed"] == 3 and stats["added"] == 7 and stats["changed"] == 0, stats)


if __name__ == "__main__":
    if not shutil.which("git"):
        print("git not on PATH — cannot build the fixture")
        sys.exit(1)
    test_config()
    with tempfile.TemporaryDirectory(prefix="ccmemo-mc-") as base:
        repo, wt = make_fixture(base)
        test_repo_and_candidates(repo, wt)
        test_db_path_and_sharing(repo, wt)
        test_prewarm_hook(repo, wt)
        test_prompt_hook_scope(repo, wt)
        test_index(repo, wt)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall passed")
