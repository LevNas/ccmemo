#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastembed>=0.3",
#     "sqlite-vec>=0.1.6",
# ]
# ///
"""Build / refresh a local hybrid-search index for a ccmemo knowledge base.

The knowledge base (Markdown entries + YAML frontmatter + `- see:` links) is the
source of truth. This index is a *derived* per-machine cache: it is regenerated
from the Markdown at any time and MUST NOT be committed to git (see the .gitignore
entry for `.claude/knowledge/.index/`).

What it does
------------
- Scans the corpus. By default (`scope: kb`) that is every `*.md` under the
  knowledge root (recursively; dated subdirs OK) — unchanged since 1.0. With
  `scope: repo` in `.claude/ccmemo.json` the candidate set is every document
  git knows in the whole repository (`git ls-files`, tracked plus untracked
  files that are not ignored), filtered by extension, include / exclude
  globs and a size cap; each file gets a `kind` (`kb` for the knowledge
  root, `docs` for the rest, or the kind a `corpora` rule declares by
  longest path prefix). See hooks/lib/config.py.
- Extracts frontmatter: title, tags, status, created, type, description.
  A file without a frontmatter title takes its first `#` heading, then its
  filename.
- Extracts typed links (`- see:` / `ref:` / `amends:` / `extends:` bullets, or a
  frontmatter `related_docs:` list — plus, in the repository scope, plain
  inline Markdown links) with their kind and one-line label into an
  `edges` table, so a search can show *why* a neighbour is linked without
  opening either file (hooks/lib/edges.py owns the extraction).
- Chunks the body: one chunk per entry, plus extra `##`-section chunks for large
  entries (so long entries stay retrievable section-by-section). A file over
  `max_file_bytes` (256 KB by default) is indexed with metadata only
  (`embedded = 0`, no chunks); the lexical arm still reads it directly.
- Embeds each chunk locally with fastembed `paraphrase-multilingual-MiniLM-L12-v2` (384-dim).
  Nothing is sent to any external API.
- Upserts into a sqlite-vec DB at `<root>/../.index/kb.db` **of the main
  checkout**: the DB location is resolved through `git rev-parse
  --git-common-dir`, so a linked worktree shares the main checkout's index
  and opens it read-only (no refresh from a worktree; the search warns when
  the index is older than the files). `CCMEMO_KB_INDEX=<file>` overrides
  the location (and lifts the read-only rule: an explicit path is yours).
- Incremental: stores a sha256 of each entry's raw text; only changed entries are
  re-embedded, deleted entries are purged. Idempotent.

Usage
-----
    # Preferred: self-contained run via uv (installs deps into an ephemeral env,
    # downloads the ~100MB model once into the fastembed cache).
    uv run scripts/kb_index.py /path/to/.claude/knowledge/entries/

    # Or with deps already installed in the active interpreter:
    python3 scripts/kb_index.py /path/to/.claude/knowledge/entries/

Dependencies
------------
    fastembed   (local ONNX embeddings; pulls paraphrase-multilingual-MiniLM-L12-v2 on first run)
    sqlite-vec  (vector KNN inside sqlite)

The library functions here are imported by kb_search.py for lazy re-indexing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


def _nixos_libstdcxx_preflight() -> None:
    """NixOS: re-exec with libstdc++ on LD_LIBRARY_PATH if numpy cannot load it.

    numpy's manylinux wheel (a transitive dep of fastembed) needs libstdc++.so.6,
    which the Nix-native CPython cannot resolve: that interpreter is not loaded
    through the nix-ld shim, so NIX_LD_LIBRARY_PATH is never consulted and the
    import dies with "libstdc++.so.6: cannot open shared object file". Plain
    LD_LIBRARY_PATH is the only effective channel, so re-exec once with it
    pointing at gcc's libstdc++ directory. No-op on other platforms and on
    already-working setups. See https://github.com/LevNas/ccmemo/issues/13.
    """
    import os

    if not os.path.exists("/etc/NIXOS") or os.environ.get("CCMEMO_LIBSTDCXX_REEXEC"):
        return
    try:
        import numpy  # noqa: F401
        return
    except ImportError as exc:
        if "libstdc++" not in str(exc):
            return

    import shutil
    import subprocess

    gcc = shutil.which("gcc")
    if not gcc:
        return  # nothing we can do; let the import error surface at first use
    try:
        libstdcxx = subprocess.run(
            [gcc, "-print-file-name=libstdc++.so.6"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return
    if not libstdcxx.startswith("/"):
        return  # gcc echoes the bare name back when it cannot resolve the file

    env = dict(os.environ)
    libdir = os.path.dirname(libstdcxx)
    env["LD_LIBRARY_PATH"] = (
        f"{libdir}:{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else libdir
    )
    env["CCMEMO_LIBSTDCXX_REEXEC"] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


_nixos_libstdcxx_preflight()

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM = 384
# Entries longer than this many characters also get per-`##`-section chunks.
LARGE_ENTRY_CHARS = 1200
# sentence-transformers paraphrase-multilingual-MiniLM needs NO task prefix
# (unlike e5, which wanted "passage:"/"query:"). Keep empty so embed_* stay generic.
PASSAGE_PREFIX = ""
QUERY_PREFIX = ""
# Environment override for the index file (absolute path). Lifts the
# read-only rule for linked worktrees: an explicit path is the caller's own.
INDEX_ENV = "CCMEMO_KB_INDEX"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

@dataclass
class Entry:
    path: Path           # absolute path
    relpath: str         # index key: relative to the key base (the knowledge
                         # root in `scope: kb`, the repository root in `scope: repo`)
    title: str
    tags: list[str]
    status: str
    created: str
    type: str
    see: list[str]       # link targets (all kinds), kept for one-hop expansion
    body: str            # body text (frontmatter stripped)
    sha256: str
    description: str = ""                # frontmatter trigger condition
    handle: str = ""                     # shortest corpus-unique short name (assign_handles)
    id: str = ""                         # frontmatter id (uuid4): identity across renames/copies
    generated_by: str = ""               # schema 3 trust family (hooks/lib/trust.py)
    generated_at: str = ""
    verified_tier: str = ""              # '' | machine | human — derived from verified
    verified_at: str = ""
    kind: str = "kb"                     # corpus kind (config.kind_of); `kb` in scope kb
    size_bytes: int = 0                  # raw size; over the cap → metadata only
    embedded: bool = True                # False when the size cap skipped the embedding
    edges: list[dict] = field(default_factory=list)  # [{target, rel, label}]
    chunks: list[tuple[str, str]] = field(default_factory=list)  # (chunk_id, text)

# One frontmatter parser for every reader (hooks/lib/frontmatter.py): reads
# both tag forms (quoted string and YAML list) and defaults a missing status
# to "active", so the index, the graph CLI and the prompt hook agree.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import frontmatter as _frontmatter  # noqa: E402
from lib import edges as _edges  # noqa: E402
from lib import trust as _trust  # noqa: E402
from lib import config as _config  # noqa: E402
from lib import repo as _repo  # noqa: E402

# Bump when the *derived* columns/tables change shape. An older DB is upgraded
# in place by re-reading metadata + edges from the Markdown — no re-embedding.
# 3: entries.handle (shortest corpus-unique short name for the summary mode).
# 4: entries.id / generated_by / generated_at / verified_tier / verified_at
#    (schema_version 3 of the entry format) and the `moves` table.
# 5: entries.kind / size_bytes / embedded (multi-corpus index, 1.28) and
#    meta.scope — the scope the keys were written under.
SCHEMA_VERSION = 5


# --------------------------------------------------------------------------- #
# Root context: where the corpus is, where the index lives, which scope
# --------------------------------------------------------------------------- #

@dataclass
class RootContext:
    root: Path                        # the knowledge root the caller named (absolute)
    cfg: _config.IndexConfig
    repo_root: Path | None            # this checkout's toplevel (None outside git)
    key_base: Path                    # relpaths are relative to this
    db_path: Path
    shared: bool                      # DB belongs to another checkout: open read-only

    @property
    def scope(self) -> str:
        return self.cfg.scope

    def extractors(self) -> tuple[str, ...]:
        return _edges.ALL_EXTRACTORS if self.scope == "repo" else _edges.DEFAULT_EXTRACTORS

    def repo_rel(self, path: Path) -> str | None:
        """Repository-relative POSIX path of *path*, or None outside the repo."""
        if self.repo_root is None:
            return None
        try:
            return path.resolve().relative_to(self.repo_root).as_posix()
        except ValueError:
            return None

    def key_of(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.key_base).as_posix()
        except ValueError:
            return None

    def kind_of(self, path: Path) -> str:
        if self.scope != "repo":
            return "kb"
        rel = self.repo_rel(path)
        return self.cfg.kind_of(rel) if rel is not None else "docs"


def _config_repo_root(root: Path) -> Path | None:
    """Outside git, the directory holding `.claude/ccmemo.json` is the repository."""
    for cand in (root, *root.parents):
        if (cand / _config.CONFIG_RELPATH).is_file():
            return cand
    return None


def context(root: Path) -> RootContext:
    """Resolve everything about *root* once: config, scope, key base, DB path.

    Cheap (git answers are cached per process; the config is one small JSON
    read), so callers may build it per operation rather than pass it around.
    """
    root = Path(root).expanduser().resolve()
    top = _repo.toplevel(str(root))
    repo_root = Path(top) if top else _config_repo_root(root)
    kb_rel = None
    if repo_root is not None:
        try:
            kb_rel = root.relative_to(repo_root).as_posix()
        except ValueError:
            kb_rel = None
    cfg = _config.index_config(str(repo_root) if repo_root else None, kb_rel)
    if cfg.scope == "repo" and repo_root is None:
        print("ccmemo index: scope 'repo' needs a git repository (or a "
              ".claude/ccmemo.json above the root) — indexing the knowledge base only",
              file=sys.stderr)
        cfg.scope = "kb"
    key_base = repo_root if cfg.scope == "repo" else root
    override = os.environ.get(INDEX_ENV, "").strip()
    if override:
        db_path, shared = Path(override).expanduser().resolve(), False
    elif top and _repo.is_linked_worktree(str(root)):
        main = _repo.main_checkout(str(root))
        rel = _repo.in_repo_relpath(str(root.parent))
        if main and rel is not None:
            db_path, shared = Path(main) / rel / ".index" / "kb.db", True
        else:  # cannot place it under the main checkout: fall back to local
            db_path, shared = root.parent / ".index" / "kb.db", False
    else:
        db_path, shared = root.parent / ".index" / "kb.db", False
    return RootContext(root=root, cfg=cfg, repo_root=repo_root, key_base=key_base,
                       db_path=db_path, shared=shared)


def index_db_path(root: Path) -> Path:
    """The index file for *root*: `<root>/../.index/kb.db` in the checkout that
    owns `.git` (the main checkout, also from a linked worktree), or
    `$CCMEMO_KB_INDEX` when set."""
    return context(root).db_path


def index_is_shared(root: Path) -> bool:
    """True when the index belongs to another checkout (this is a linked
    worktree without `CCMEMO_KB_INDEX`): readers open it read-only and
    never refresh it from here."""
    return context(root).shared


def key_base(root: Path) -> Path:
    """The directory index relpaths are relative to (see Entry.relpath)."""
    return context(root).key_base


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """Return (normalized frontmatter, body). Kept as the module-level entry
    point kb_search.py imports; delegates to the shared parser."""
    return _frontmatter.parse(content)


def content_sha256(content: str) -> str:
    """The change-detection hash: over the decoded text (newlines
    normalised by the reader), as every index since 1.0 has computed it."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


_H1_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")


def _first_h1(body: str) -> str:
    m = _H1_RE.search(body)
    return " ".join(m.group(1).split()) if m else ""


def parse_entry(path: Path, root: Path, ctx: RootContext | None = None) -> Entry | None:
    ctx = ctx or context(root)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        size = path.stat().st_size
    except OSError:
        return None

    fm, body = _split_frontmatter(content)
    sha = content_sha256(content)
    tags = fm["tags"]
    edges = _extract_entry_edges(ctx, path.parent, fm, body)
    see = [e["target"] for e in edges]
    relpath = ctx.key_of(path) or path.name

    gen = _trust.generated_of(fm)
    tier, tier_at = _trust.verified_tier(fm)
    entry = Entry(
        path=path,
        relpath=relpath,
        title=fm["title"] or _first_h1(body) or path.stem,
        tags=tags,
        status=fm["status"],
        created=fm["created"],
        type=fm["type"],
        see=see,
        body=body,
        sha256=sha,
        description=fm["description"],
        edges=edges,
        id=str(fm.get("id", "") or "").strip(),
        generated_by=gen["by"] if gen else "",
        generated_at=gen["at"] if gen else "",
        verified_tier=tier,
        verified_at=tier_at,
        kind=ctx.kind_of(path),
        size_bytes=size,
    )
    if entry.size_bytes > ctx.cfg.max_file_bytes:
        entry.embedded = False
        entry.chunks = []
    else:
        entry.chunks = _chunk_entry(entry)
    return entry


def _extract_entry_edges(ctx: RootContext, entry_dir: Path, fm: dict, body: str) -> list[dict]:
    """Typed edges (kept even when unresolved, so lint can see a broken link)
    plus — in the repository scope — inline Markdown links (dropped unless
    they resolve to an indexed file). Merged by (target, rel), first wins."""
    typed = _resolve_edges(ctx, entry_dir, _edges.extract_edges(fm, body, _edges.DEFAULT_EXTRACTORS))
    out, seen = [], set()
    for e in typed:
        key = (e["target"], e["rel"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    if "markdown-links" in ctx.extractors():
        inline = _edges.extract_edges(fm, body, ("markdown-links",))
        for e in _resolve_edges(ctx, entry_dir, inline, drop_unresolved=True):
            key = (e["target"], e["rel"])
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


def _resolve_edges(ctx: RootContext, entry_dir: Path, edges: list[dict],
                   drop_unresolved: bool = False) -> list[dict]:
    """Rewrite each edge target as an index key when it resolves.

    Links are written root-relative by convention (`2026/09/...md`); a target
    that only resolves relative to the linking file's directory, or to the
    repository root (design-document `related_docs`), is normalised to its
    key too. Unresolvable targets are kept as written so a broken link is
    still visible in the summary output (kb_graph lint reports it) — unless
    `drop_unresolved` (inline Markdown links, which point at code, images
    and the web as often as at documents).
    """
    out: list[dict] = []
    bases = [ctx.root, entry_dir]
    if ctx.repo_root is not None:
        bases.append(ctx.repo_root)
    for e in edges:
        target = e["target"]
        resolved = None
        for base in bases:
            cand = base / target
            try:
                cand = cand.resolve()
                if cand.is_file():
                    resolved = cand
                    break
            except (OSError, ValueError):
                continue
        key = ctx.key_of(resolved) if resolved is not None else None
        if key is not None and drop_unresolved:
            rel = ctx.repo_rel(resolved)
            if rel is not None and not ctx.cfg.is_candidate(rel):
                key = None  # exists, but is not a document this index holds
        if key is None:
            if drop_unresolved:
                continue
            key = target
        out.append({"target": key, "rel": e["rel"], "label": e["label"]})
    return out


def _chunk_entry(entry: Entry) -> list[tuple[str, str]]:
    """Whole-entry chunk + per-`##`-section chunks for large entries.

    chunk_id is stable for a given entry+section so upserts are deterministic.
    The title is prepended to every chunk to anchor the embedding.
    """
    chunks: list[tuple[str, str]] = []
    title = entry.title.strip()
    full = f"{title}\n\n{entry.body}".strip()
    chunks.append(("full", full))

    if len(entry.body) <= LARGE_ENTRY_CHARS:
        return chunks

    # Split on level-2 headings, keeping the heading with its section.
    parts = re.split(r"(?m)^(##\s+.*)$", entry.body)
    # parts = [pre, heading1, body1, heading2, body2, ...]
    section_idx = 0
    i = 1
    while i < len(parts) - 1:
        heading = parts[i].strip()
        section_body = parts[i + 1].strip()
        text = f"{title} — {heading.lstrip('# ').strip()}\n\n{section_body}".strip()
        if section_body:
            chunks.append((f"sec{section_idx}", text))
            section_idx += 1
        i += 2
    return chunks


def candidate_files(root: Path, ctx: RootContext | None = None) -> list[tuple[Path, str]]:
    """The files this index covers, as (absolute path, index key), sorted by key.

    `scope: kb` — every `*.md` under the knowledge root except `CLAUDE.md`
    (the pre-1.28 set; a repository's `exclude` globs still apply).
    `scope: repo` — what git knows under the repository root (tracked and
    untracked-not-ignored; nested repositories and worktrees are not
    descended into), narrowed by the extension allowlist, the include and
    exclude globs. Without git the tree is walked instead. No file is read.
    """
    ctx = ctx or context(root)
    found: list[tuple[Path, str]] = []
    if ctx.scope != "repo":
        for path in sorted(ctx.root.rglob("*.md")):
            if path.name == "CLAUDE.md" or not path.is_file():
                continue
            rel = ctx.repo_rel(path)
            if rel is not None and ctx.cfg.excluded(rel):
                continue
            key = ctx.key_of(path)
            if key is not None:
                found.append((path, key))
        return found
    assert ctx.repo_root is not None
    rels = _repo.ls_files(str(ctx.repo_root))
    if rels is None:
        rels = []
        for dirpath, dirnames, filenames in os.walk(ctx.repo_root):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for fn in filenames:
                p = Path(dirpath) / fn
                rels.append(p.relative_to(ctx.repo_root).as_posix())
    for rel in sorted(rels):
        if not ctx.cfg.is_candidate(rel):
            continue
        path = ctx.repo_root / rel
        if not path.is_file():
            continue  # deleted but still in the git index
        found.append((path, rel))
    return found


def scan_entries(root: Path, ctx: RootContext | None = None) -> dict[str, Entry]:
    """Return {key: Entry} for every candidate file (see candidate_files)."""
    ctx = ctx or context(root)
    entries: dict[str, Entry] = {}
    for path, _key in candidate_files(root, ctx):
        entry = parse_entry(path, root, ctx)
        if entry is not None:
            entries[entry.relpath] = entry
    assign_handles(entries)
    return entries


_HANDLE_PREFIX_RE = re.compile(r"^(\d{8}-\d{6})-")


def handle_candidates(relpath: str) -> list[str]:
    """Short names for one entry, shortest first: the `YYYYMMDD-HHMMSS`
    filename prefix (when the file has one), the basename, the relpath."""
    name = relpath.rsplit("/", 1)[-1]
    cands: list[str] = []
    m = _HANDLE_PREFIX_RE.match(name)
    if m:
        cands.append(m.group(1))
    cands.append(name)
    if relpath != name:
        cands.append(relpath)
    return cands


def assign_handles(entries: dict[str, Entry]) -> None:
    """Give every entry the shortest name that is unique in this corpus.

    The date-time prefix is unique for most entries but not by construction:
    entries recorded in the same second share it (a batch of proposals
    written together). Those fall back to the basename, then the relpath,
    so a summary line always names one file and `kb_graph.py` (which
    resolves a unique filename substring) accepts the handle as printed.
    A handle depends on the whole corpus, so it is assigned after the scan
    and written by `_write_handles`, not by the per-entry upsert.
    """
    cands = {rp: handle_candidates(rp) for rp in entries}
    counts: Counter[str] = Counter()
    for cs in cands.values():
        counts.update(cs)
    for rp, entry in entries.items():
        entry.handle = next((c for c in cands[rp] if counts[c] == 1), rp)


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def connect(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    import sqlite_vec

    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def open_index(root: Path) -> sqlite3.Connection | None:
    """A connection for *reading* the index of *root*, or None when there is
    no index yet. From a linked worktree the shared DB is opened read-only
    and its schema is left alone; otherwise the schema is brought up to date."""
    ctx = context(root)
    if not ctx.db_path.exists():
        return None
    conn = connect(ctx.db_path, readonly=ctx.shared)
    if not ctx.shared:
        init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            relpath  TEXT PRIMARY KEY,
            title    TEXT,
            tags     TEXT,   -- JSON list
            status   TEXT,
            created  TEXT,
            type     TEXT,
            see      TEXT,   -- JSON list of relpaths
            sha256   TEXT
        )
        """
    )
    _ensure_column(conn, "entries", "description", "TEXT DEFAULT ''")
    _ensure_column(conn, "entries", "handle", "TEXT DEFAULT ''")
    for column in ("id", "generated_by", "generated_at", "verified_tier", "verified_at"):
        _ensure_column(conn, "entries", column, "TEXT DEFAULT ''")
    _ensure_column(conn, "entries", "kind", "TEXT DEFAULT ''")
    _ensure_column(conn, "entries", "size_bytes", "INTEGER DEFAULT 0")
    _ensure_column(conn, "entries", "embedded", "INTEGER DEFAULT 1")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS moves (
            id          TEXT,   -- entry id seen at a new relpath
            old_relpath TEXT,   -- where the index last had it (file gone)
            new_relpath TEXT,
            at          INTEGER -- unix time the move was detected
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
            src    TEXT,    -- linking entry relpath
            target TEXT,    -- linked entry relpath (as resolved; raw if broken)
            rel    TEXT,    -- see | ref | amends | extends
            label  TEXT,    -- one-line reason written after the link
            ord    INTEGER  -- position within src, document order
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS edges_src ON edges (src)")
    conn.execute("CREATE INDEX IF NOT EXISTS edges_target ON edges (target)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            rowid    INTEGER PRIMARY KEY AUTOINCREMENT,
            relpath  TEXT,
            chunk_id TEXT,
            text     TEXT,
            UNIQUE(relpath, chunk_id)
        )
        """
    )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            chunk_rowid INTEGER PRIMARY KEY,
            embedding FLOAT[{EMBED_DIM}]
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _meta_get(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _stored_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row[0]: row[1]
        for row in conn.execute("SELECT relpath, sha256 FROM entries")
    }


# --------------------------------------------------------------------------- #
# Embedding
# --------------------------------------------------------------------------- #

_EMBEDDER = None


def get_embedder():
    """Lazily construct the fastembed model (downloads ~220MB on first ever use)."""
    global _EMBEDDER
    if _EMBEDDER is None:
        from fastembed import TextEmbedding

        _EMBEDDER = TextEmbedding(model_name=EMBED_MODEL)
    return _EMBEDDER


def embed_passages(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    prefixed = [PASSAGE_PREFIX + t for t in texts]
    return [vec.tolist() for vec in model.embed(prefixed)]


def embed_query(text: str) -> list[float]:
    model = get_embedder()
    return next(iter(model.embed([QUERY_PREFIX + text]))).tolist()


# --------------------------------------------------------------------------- #
# Index maintenance
# --------------------------------------------------------------------------- #

def serialize_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _delete_entry_rows(conn: sqlite3.Connection, relpath: str) -> None:
    rows = [
        r[0]
        for r in conn.execute(
            "SELECT rowid FROM chunks WHERE relpath = ?", (relpath,)
        )
    ]
    for rowid in rows:
        conn.execute("DELETE FROM vec_chunks WHERE chunk_rowid = ?", (rowid,))
    conn.execute("DELETE FROM chunks WHERE relpath = ?", (relpath,))
    conn.execute("DELETE FROM edges WHERE src = ?", (relpath,))
    conn.execute("DELETE FROM entries WHERE relpath = ?", (relpath,))


def _write_edges(conn: sqlite3.Connection, entry: Entry) -> None:
    conn.execute("DELETE FROM edges WHERE src = ?", (entry.relpath,))
    conn.executemany(
        "INSERT INTO edges (src, target, rel, label, ord) VALUES (?, ?, ?, ?, ?)",
        [
            (entry.relpath, e["target"], e["rel"], e["label"], i)
            for i, e in enumerate(entry.edges)
        ],
    )


def _has_chunks(conn: sqlite3.Connection, relpath: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM chunks WHERE relpath = ? LIMIT 1", (relpath,)
    ).fetchone() is not None


def _write_metadata(conn: sqlite3.Connection, entry: Entry) -> None:
    """Refresh the derived (non-embedding) columns and edges of one entry.

    `embedded` reflects the rows that exist, not the current size cap: a
    file that was embedded before a cap was lowered keeps its vectors until
    its content changes (a metadata upgrade never deletes embeddings)."""
    conn.execute(
        "UPDATE entries SET title = ?, tags = ?, status = ?, created = ?, type = ?, "
        "see = ?, description = ?, handle = ?, id = ?, generated_by = ?, generated_at = ?, "
        "verified_tier = ?, verified_at = ?, kind = ?, size_bytes = ?, embedded = ? "
        "WHERE relpath = ?",
        (
            entry.title,
            json.dumps(entry.tags, ensure_ascii=False),
            entry.status,
            entry.created,
            entry.type,
            json.dumps(entry.see, ensure_ascii=False),
            entry.description,
            entry.handle,
            entry.id,
            entry.generated_by,
            entry.generated_at,
            entry.verified_tier,
            entry.verified_at,
            entry.kind,
            entry.size_bytes,
            1 if _has_chunks(conn, entry.relpath) else 0,
            entry.relpath,
        ),
    )
    _write_edges(conn, entry)


def _write_handles(conn: sqlite3.Connection, entries: dict[str, Entry]) -> int:
    """Refresh `entries.handle` for the whole corpus. Kept apart from the
    per-entry upsert because a handle depends on every other entry: adding
    one that shares a prefix changes the *other* entry's handle although
    its content (sha256) is unchanged. Returns the number of rows changed."""
    n = 0
    for relpath, entry in entries.items():
        cur = conn.execute(
            "UPDATE entries SET handle = ? WHERE relpath = ? AND handle IS NOT ?",
            (entry.handle, relpath, entry.handle),
        )
        n += cur.rowcount
    return n


def _backfill_metadata(conn: sqlite3.Connection, entries: dict[str, Entry]) -> int:
    """Rewrite metadata + edges for every stored entry that is still on disk.
    Embeddings are untouched. Returns the number refreshed."""
    n = 0
    for relpath in _stored_hashes(conn):
        entry = entries.get(relpath)
        if entry is None:
            continue
        _write_metadata(conn, entry)
        n += 1
    _write_handles(conn, entries)
    return n


def _upgrade_schema(conn: sqlite3.Connection, entries: dict[str, Entry]) -> int:
    """Backfill metadata + edges for every indexed entry when the DB predates
    SCHEMA_VERSION. Embeddings are untouched. Returns the number refreshed."""
    if _meta_get(conn, "schema_version") == str(SCHEMA_VERSION):
        return 0
    n = _backfill_metadata(conn, entries)
    _meta_set(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()
    return n


def _rekey_scope(conn: sqlite3.Connection, ctx: RootContext,
                 entries: dict[str, Entry]) -> int:
    """Re-key stored rows when the scope changed since the index was built.

    `scope: kb` keys rows by knowledge-root relpath, `scope: repo` by
    repository relpath, so switching moves every knowledge entry to a new
    key. Rows whose content (sha256) is found at exactly one new key are
    re-keyed with their embeddings; the rest are re-embedded by the normal
    add / remove pass. Recorded moves are dropped (their paths belong to the
    old scope) and edges are rewritten from the Markdown. Returns the
    number of rows re-keyed."""
    stored_scope = _meta_get(conn, "scope") or "kb"  # indexes before 1.28 are kb
    if stored_scope == ctx.scope:
        return 0
    stored = _stored_hashes(conn)
    by_sha: dict[str, list[str]] = {}
    for key, e in entries.items():
        if key not in stored:
            by_sha.setdefault(e.sha256, []).append(key)
    # The scope translation of a knowledge entry's key: `2026/09/x.md` in
    # scope kb is `<kb path>/2026/09/x.md` in scope repo. Taken first, so a
    # byte-identical mirror elsewhere cannot capture the entry's row.
    kb_prefix = ctx.repo_rel(ctx.root) if ctx.repo_root is not None else None

    def translated(old: str) -> str | None:
        if not kb_prefix:
            return None
        if ctx.scope == "repo":
            return f"{kb_prefix}/{old}"
        if old.startswith(kb_prefix + "/"):
            return old[len(kb_prefix) + 1:]
        return None

    n = 0
    for old_key, sha in list(stored.items()):
        if old_key in entries:
            continue
        cands = [k for k in by_sha.get(sha, []) if k not in stored]
        want = translated(old_key)
        if want in cands:
            cands = [want]
        if len(cands) != 1:
            continue
        new_key = cands[0]
        for sql in ("UPDATE entries SET relpath = ? WHERE relpath = ?",
                    "UPDATE chunks SET relpath = ? WHERE relpath = ?"):
            conn.execute(sql, (new_key, old_key))
        stored[new_key] = stored.pop(old_key)
        by_sha[sha] = [k for k in by_sha.get(sha, []) if k != new_key]
        n += 1
    conn.execute("DELETE FROM moves")
    conn.execute("DELETE FROM edges")
    _backfill_metadata(conn, entries)
    _meta_set(conn, "scope", ctx.scope)
    conn.commit()
    return n


def ensure_metadata(root: Path) -> int:
    """Upgrade an older index in place (metadata + edges only, no embedding).

    kb_search calls this before searching so the summary mode has edges even
    when no entry changed since the DB was built. Cheap: parses the Markdown
    once, touches sqlite only when the schema version or the scope is behind.
    A shared (worktree) index is never touched from here.
    """
    ctx = context(root)
    if ctx.shared or not ctx.db_path.exists():
        return 0
    conn = connect(ctx.db_path)
    init_schema(conn)
    try:
        if (_meta_get(conn, "schema_version") == str(SCHEMA_VERSION)
                and (_meta_get(conn, "scope") or "kb") == ctx.scope):
            return 0
        entries = scan_entries(root, ctx)
        _rekey_scope(conn, ctx, entries)
        return _upgrade_schema(conn, entries)
    finally:
        conn.close()


def _upsert_entry(conn: sqlite3.Connection, entry: Entry) -> int:
    """(Re)write one entry and its chunks. Returns number of chunks embedded."""
    _delete_entry_rows(conn, entry.relpath)
    conn.execute(
        "INSERT INTO entries (relpath, title, tags, status, created, type, see, sha256, "
        "description, handle, id, generated_by, generated_at, verified_tier, verified_at, "
        "kind, size_bytes, embedded) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry.relpath,
            entry.title,
            json.dumps(entry.tags, ensure_ascii=False),
            entry.status,
            entry.created,
            entry.type,
            json.dumps(entry.see, ensure_ascii=False),
            entry.sha256,
            entry.description,
            entry.handle,
            entry.id,
            entry.generated_by,
            entry.generated_at,
            entry.verified_tier,
            entry.verified_at,
            entry.kind,
            entry.size_bytes,
            1 if entry.chunks else 0,
        ),
    )
    _write_edges(conn, entry)
    texts = [text for _, text in entry.chunks]
    if not texts:
        return 0
    vectors = embed_passages(texts)
    for (chunk_id, text), vec in zip(entry.chunks, vectors):
        cur = conn.execute(
            "INSERT INTO chunks (relpath, chunk_id, text) VALUES (?, ?, ?)",
            (entry.relpath, chunk_id, text),
        )
        rowid = cur.lastrowid
        conn.execute(
            "INSERT INTO vec_chunks (chunk_rowid, embedding) VALUES (?, ?)",
            (rowid, serialize_f32(vec)),
        )
    return len(texts)


def _apply_moves(conn: sqlite3.Connection, base: Path, entries: dict[str, Entry],
                 stored: dict[str, str]) -> int:
    """Re-key entries whose `id` the index already holds under a relpath
    that no longer exists on disk: a move (manual `mv`, a rename done
    outside `kb_graph.py rename`). Rows keep their embeddings — the content
    is compared by sha256 afterwards as usual — and the move is recorded in
    `moves` so `kb_graph.py relink` can repair links that still point at
    the old path. `base` is the directory the keys are relative to.
    Returns the number of moves applied."""
    known = {eid: rp for rp, eid in conn.execute(
        "SELECT relpath, id FROM entries WHERE id != ''")}
    n = 0
    for relpath, entry in entries.items():
        if relpath in stored or not entry.id:
            continue
        old = known.get(entry.id)
        if not old or old == relpath or (base / old).exists():
            continue  # unknown, or the old file still exists (a copy, not a move)
        for sql in ("UPDATE entries SET relpath = ? WHERE relpath = ?",
                    "UPDATE chunks SET relpath = ? WHERE relpath = ?",
                    "UPDATE edges SET src = ? WHERE src = ?",
                    "UPDATE edges SET target = ? WHERE target = ?"):
            conn.execute(sql, (relpath, old))
        conn.execute(
            "INSERT INTO moves (id, old_relpath, new_relpath, at) VALUES (?, ?, ?, ?)",
            (entry.id, old, relpath, int(time.time())),
        )
        stored[relpath] = stored.pop(old)
        known[entry.id] = relpath
        n += 1
    return n


class SharedIndexError(RuntimeError):
    """Raised when a write is attempted on an index owned by another checkout."""


def reindex(root: Path, *, only: set[str] | None = None, verbose: bool = True) -> dict:
    """Incrementally refresh the index for `root`.

    If `only` is given, restrict the scan/refresh to those relpaths (used by the
    search-time lazy refresh). Returns a small stats dict.
    """
    ctx = context(root)
    if ctx.shared:
        raise SharedIndexError(
            f"index {ctx.db_path} belongs to the main checkout; refresh it from there "
            f"(or set {INDEX_ENV} to an index of this worktree's own)")
    db_path = ctx.db_path
    conn = connect(db_path)
    init_schema(conn)

    entries = scan_entries(root, ctx)
    corpus = entries  # the full scan: handles are corpus-wide
    rekeyed = _rekey_scope(conn, ctx, entries)
    upgraded = _upgrade_schema(conn, entries)
    stored = _stored_hashes(conn)

    if only is not None:
        entries = {k: v for k, v in entries.items() if k in only}

    moved = _apply_moves(conn, ctx.key_base, entries, stored)
    added = changed = removed = unchanged = 0
    embedded_chunks = 0
    skipped = 0

    # Removals (skip when scoped to `only`, since we did not scan everything).
    if only is None:
        for relpath in list(stored):
            if relpath not in entries:
                _delete_entry_rows(conn, relpath)
                removed += 1

    for relpath, entry in entries.items():
        prior = stored.get(relpath)
        if prior == entry.sha256:
            unchanged += 1
            continue
        embedded_chunks += _upsert_entry(conn, entry)
        if not entry.embedded:
            skipped += 1
        if prior is None:
            added += 1
        else:
            changed += 1

    # A neighbour's handle can change without its content changing (a new
    # entry took the same prefix), so handles are refreshed for the corpus.
    _write_handles(conn, corpus)
    _meta_set(conn, "last_indexed", str(int(time.time())))
    _meta_set(conn, "schema_version", str(SCHEMA_VERSION))
    _meta_set(conn, "scope", ctx.scope)
    conn.commit()
    conn.close()

    stats = {
        "added": added,
        "changed": changed,
        "removed": removed,
        "moved": moved,
        "unchanged": unchanged,
        "embedded_chunks": embedded_chunks,
        "metadata_only": skipped,
        "upgraded": upgraded,
        "rekeyed": rekeyed,
        "scope": ctx.scope,
        "db": str(db_path),
    }
    if verbose:
        extra = f", {upgraded} metadata rows upgraded" if upgraded else ""
        extra += f", {rekeyed} rows re-keyed for scope {ctx.scope}" if rekeyed else ""
        extra += f", {moved} moved (rows re-keyed, not re-embedded)" if moved else ""
        extra += f", {skipped} over {ctx.cfg.max_file_bytes} bytes (metadata only)" if skipped else ""
        print(
            f"index[{ctx.scope}]: +{added} ~{changed} -{removed} ={unchanged} "
            f"({embedded_chunks} chunks embedded{extra}) -> {db_path}"
        )
    return stats


def detect_stale(root: Path) -> set[str]:
    """Return relpaths whose on-disk sha256 differs from the index (or are new)."""
    ctx = context(root)
    if not ctx.db_path.exists():
        # Whole base is stale.
        return set(scan_entries(root, ctx).keys())
    conn = connect(ctx.db_path, readonly=ctx.shared)
    if not ctx.shared:
        init_schema(conn)
    stored = _stored_hashes(conn)
    conn.close()
    stale: set[str] = set()
    for path, rel in candidate_files(root, ctx):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if stored.get(rel) != content_sha256(content):
            stale.add(rel)
    return stale


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        print("error: knowledge root (entries dir) argument required", file=sys.stderr)
        return 2
    root = Path(argv[0]).expanduser().resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    try:
        reindex(root, verbose=True)
    except SharedIndexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
