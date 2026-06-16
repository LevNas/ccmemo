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
- Scans every `*.md` under the knowledge root (recursively; dated subdirs OK).
- Extracts frontmatter: title, tags, status, created, type.
- Extracts `- see:` links from the body (relative paths to other entries).
- Chunks the body: one chunk per entry, plus extra `##`-section chunks for large
  entries (so long entries stay retrievable section-by-section).
- Embeds each chunk locally with fastembed `paraphrase-multilingual-MiniLM-L12-v2` (384-dim).
  Nothing is sent to any external API.
- Upserts into a sqlite-vec DB at `<root>/../.index/kb.db`.
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
import re
import sqlite3
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM = 384
# Entries longer than this many characters also get per-`##`-section chunks.
LARGE_ENTRY_CHARS = 1200
# sentence-transformers paraphrase-multilingual-MiniLM needs NO task prefix
# (unlike e5, which wanted "passage:"/"query:"). Keep empty so embed_* stay generic.
PASSAGE_PREFIX = ""
QUERY_PREFIX = ""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

@dataclass
class Entry:
    path: Path           # absolute path
    relpath: str         # path relative to the knowledge root (matches see: links)
    title: str
    tags: list[str]
    status: str
    created: str
    type: str
    see: list[str]       # relative paths referenced by `- see:` links
    body: str            # body text (frontmatter stripped)
    sha256: str
    chunks: list[tuple[str, str]] = field(default_factory=list)  # (chunk_id, text)


_SEE_LINK_RE = re.compile(r"^-\s+see:\s*\[[^\]]*\]\(([^)]+)\)", re.MULTILINE)
_TAG_RE = re.compile(r"#[A-Za-z][A-Za-z0-9-]*")


def _split_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Handles missing/blank frontmatter."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_block = content[3:end]
    body = content[end + 4 :].lstrip("\n")
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm, body


def parse_entry(path: Path, root: Path) -> Entry | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    fm, body = _split_frontmatter(content)
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    tags = _TAG_RE.findall(fm.get("tags", ""))
    see = [m.strip() for m in _SEE_LINK_RE.findall(body)]
    try:
        relpath = str(path.relative_to(root))
    except ValueError:
        relpath = path.name

    entry = Entry(
        path=path,
        relpath=relpath,
        title=fm.get("title", path.stem),
        tags=tags,
        status=fm.get("status", ""),
        created=fm.get("created", ""),
        type=fm.get("type", ""),
        see=see,
        body=body,
        sha256=sha,
    )
    entry.chunks = _chunk_entry(entry)
    return entry


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


def scan_entries(root: Path) -> dict[str, Entry]:
    """Return {relpath: Entry} for every entry under root (CLAUDE.md excluded)."""
    entries: dict[str, Entry] = {}
    for path in sorted(root.rglob("*.md")):
        if path.name == "CLAUDE.md":
            continue
        entry = parse_entry(path, root)
        if entry is not None:
            entries[entry.relpath] = entry
    return entries


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def index_db_path(root: Path) -> Path:
    """`<root>/../.index/kb.db`. For .../knowledge/entries this is .../knowledge/.index/kb.db."""
    return root.parent / ".index" / "kb.db"


def connect(db_path: Path) -> sqlite3.Connection:
    import sqlite_vec

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
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
    conn.execute("DELETE FROM entries WHERE relpath = ?", (relpath,))


def _upsert_entry(conn: sqlite3.Connection, entry: Entry) -> int:
    """(Re)write one entry and its chunks. Returns number of chunks embedded."""
    _delete_entry_rows(conn, entry.relpath)
    conn.execute(
        "INSERT INTO entries (relpath, title, tags, status, created, type, see, sha256) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry.relpath,
            entry.title,
            json.dumps(entry.tags, ensure_ascii=False),
            entry.status,
            entry.created,
            entry.type,
            json.dumps(entry.see, ensure_ascii=False),
            entry.sha256,
        ),
    )
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


def reindex(root: Path, *, only: set[str] | None = None, verbose: bool = True) -> dict:
    """Incrementally refresh the index for `root`.

    If `only` is given, restrict the scan/refresh to those relpaths (used by the
    search-time lazy refresh). Returns a small stats dict.
    """
    db_path = index_db_path(root)
    conn = connect(db_path)
    init_schema(conn)

    entries = scan_entries(root)
    stored = _stored_hashes(conn)

    if only is not None:
        entries = {k: v for k, v in entries.items() if k in only}

    added = changed = removed = unchanged = 0
    embedded_chunks = 0

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
        if prior is None:
            added += 1
        else:
            changed += 1

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('last_indexed', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(int(time.time())),),
    )
    conn.commit()
    conn.close()

    stats = {
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged": unchanged,
        "embedded_chunks": embedded_chunks,
        "db": str(db_path),
    }
    if verbose:
        print(
            f"index: +{added} ~{changed} -{removed} ={unchanged} "
            f"({embedded_chunks} chunks embedded) -> {db_path}"
        )
    return stats


def detect_stale(root: Path) -> set[str]:
    """Return relpaths whose on-disk sha256 differs from the index (or are new)."""
    db_path = index_db_path(root)
    if not db_path.exists():
        # Whole base is stale.
        return set(scan_entries(root).keys())
    conn = connect(db_path)
    init_schema(conn)
    stored = _stored_hashes(conn)
    conn.close()
    stale: set[str] = set()
    for path in root.rglob("*.md"):
        if path.name == "CLAUDE.md":
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if stored.get(rel) != sha:
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
    reindex(root, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
