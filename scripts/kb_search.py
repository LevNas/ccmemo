#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastembed>=0.3",
#     "sqlite-vec>=0.1.6",
# ]
# ///
"""Hybrid search over a ccmemo knowledge base.

Combines two retrieval arms and fuses them with Reciprocal Rank Fusion (RRF):

  1. lexical arm  — ripgrep over the Markdown, optionally pre-tokenising the query
                    with mecab (reusing the existing hook's approach) so Japanese
                    queries split into searchable nouns.
  2. vector arm   — KNN over locally-computed paraphrase-multilingual-MiniLM embeddings
                    stored in the sqlite-vec index built by kb_index.py.

After fusion, the top hits are expanded one hop along `see:` links (so a directly
relevant entry pulls in its explicitly-linked neighbours), then frontmatter filters
(status / tag / type / created range) are applied, and a ranked list of
path + score + snippet is returned.

Lazy refresh: before searching, on-disk sha256 hashes are compared with the index;
any entry that changed (or is new) is re-embedded just-in-time so results never go
stale. Disable with --no-lazy.

Usage
-----
    uv run scripts/kb_search.py ROOT "クエリ" [filters...]
    python3 scripts/kb_search.py ROOT "クエリ" [filters...]

      ROOT                 knowledge entries dir (same arg as kb_index.py)
      --status active      only entries with this status
      --tag '#secret-management'   require this tag (repeatable)
      --type knowledge     only this frontmatter type
      --created-from 2026-05-01    created >= date (YYYY-MM-DD)
      --created-to   2026-06-30    created <= date
      --top N              number of results (default 8)
      --no-lazy            skip search-time re-embedding of changed entries
      --no-mecab           do not run mecab tokenisation for the lexical arm
      --json               emit JSON instead of human-readable output

Depends on kb_index.py (imported) plus the same fastembed / sqlite-vec deps.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Import the sibling indexer module regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_index as kbi  # noqa: E402

RRF_K = 60
SNIPPET_CHARS = 160


# --------------------------------------------------------------------------- #
# Lexical arm (ripgrep, optional mecab tokenisation)
# --------------------------------------------------------------------------- #

def _mecab_keywords(query: str, min_len: int = 2) -> list[str]:
    """Extract content nouns from a (Japanese) query, mirroring the existing hook."""
    if not shutil.which("mecab"):
        return []
    try:
        out = subprocess.run(
            ["mecab"], input=query, capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    words: list[str] = []
    for line in out.splitlines():
        if line == "EOS" or "\t" not in line:
            continue
        surface, _, feat = line.partition("\t")
        cols = feat.split(",")
        if not cols:
            continue
        if cols[0] != "名詞":
            continue
        sub = cols[1] if len(cols) > 1 else ""
        if sub in ("非自立", "代名詞", "数", "接尾"):
            continue
        if len(surface) >= min_len:
            words.append(surface)
    # Deduplicate, preserve order.
    seen: set[str] = set()
    uniq = []
    for w in words:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq


def _ascii_terms(query: str, min_len: int = 2) -> list[str]:
    """ASCII word/identifier terms from the query (e.g. op-wrap, glab-op)."""
    import re

    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", query)
    seen: set[str] = set()
    out = []
    for t in terms:
        if len(t) >= min_len and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def lexical_rank(root: Path, query: str, use_mecab: bool) -> list[str]:
    """Return entry relpaths ranked by lexical relevance (best first).

    Scoring mirrors the existing hook: each matching term contributes 1/hit_count
    to every file it matched, so rare terms weigh more. Terms matching too many
    files are skipped as non-discriminating.
    """
    if not shutil.which("rg"):
        return []

    terms: list[str] = []
    if use_mecab:
        terms.extend(_mecab_keywords(query))
    terms.extend(_ascii_terms(query))
    # Fall back to whitespace splitting if tokenisers produced nothing.
    if not terms:
        terms = [t for t in query.split() if len(t) >= 2]
    # Deduplicate case-insensitively.
    seen: set[str] = set()
    uniq_terms = []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq_terms.append(t)

    hit_count_limit = 50
    scores: dict[str, float] = {}
    for term in uniq_terms:
        try:
            res = subprocess.run(
                ["rg", "-l", "--fixed-strings", "--ignore-case", "--", term, str(root)],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        files = [ln for ln in res.stdout.splitlines() if ln.strip()]
        n = len(files)
        if n == 0 or n > hit_count_limit:
            continue
        for f in files:
            p = Path(f)
            if p.name == "CLAUDE.md":
                continue
            try:
                rel = str(p.relative_to(root))
            except ValueError:
                continue
            scores[rel] = scores.get(rel, 0.0) + 1.0 / n

    return [rel for rel, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


# --------------------------------------------------------------------------- #
# Vector arm (sqlite-vec KNN)
# --------------------------------------------------------------------------- #

def vector_rank(root: Path, query: str, k: int = 40) -> list[str]:
    """Return entry relpaths ranked by best (closest) chunk distance, best first."""
    db_path = kbi.index_db_path(root)
    if not db_path.exists():
        return []
    qvec = kbi.embed_query(query)
    conn = kbi.connect(db_path)
    kbi.init_schema(conn)
    rows = conn.execute(
        """
        SELECT c.relpath, v.distance
        FROM vec_chunks v
        JOIN chunks c ON c.rowid = v.chunk_rowid
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
        """,
        (kbi.serialize_f32(qvec), k),
    ).fetchall()
    conn.close()
    # Keep the best (smallest) distance per entry, preserve ascending order.
    best: dict[str, float] = {}
    for relpath, dist in rows:
        if relpath not in best or dist < best[relpath]:
            best[relpath] = dist
    return [rel for rel, _ in sorted(best.items(), key=lambda kv: kv[1])]


# --------------------------------------------------------------------------- #
# Fusion + see: expansion + filters
# --------------------------------------------------------------------------- #

def rrf_fuse(*ranked_lists: list[str], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion. Returns {relpath: fused_score}."""
    fused: dict[str, float] = {}
    for ranking in ranked_lists:
        for rank, relpath in enumerate(ranking):
            fused[relpath] = fused.get(relpath, 0.0) + 1.0 / (k + rank + 1)
    return fused


def expand_see_one_hop(root: Path, top: list[str], fused: dict[str, float]) -> None:
    """Pull in entries directly linked from `top` (mutates `fused`).

    A neighbour reachable from a top hit gets a small boost (half the linking
    entry's score) so it can surface even if neither arm ranked it directly.
    """
    db_path = kbi.index_db_path(root)
    if not db_path.exists():
        return
    conn = kbi.connect(db_path)
    kbi.init_schema(conn)
    known = {row[0] for row in conn.execute("SELECT relpath FROM entries")}
    for relpath in top:
        row = conn.execute(
            "SELECT see FROM entries WHERE relpath = ?", (relpath,)
        ).fetchone()
        if not row or not row[0]:
            continue
        try:
            neighbours = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        for nb in neighbours:
            if nb in known and nb not in fused:
                fused[nb] = 0.5 * fused.get(relpath, 0.0)
    conn.close()


def _entry_meta(root: Path) -> dict[str, dict]:
    db_path = kbi.index_db_path(root)
    meta: dict[str, dict] = {}
    if not db_path.exists():
        return meta
    conn = kbi.connect(db_path)
    kbi.init_schema(conn)
    for relpath, title, tags, status, created, etype in conn.execute(
        "SELECT relpath, title, tags, status, created, type FROM entries"
    ):
        meta[relpath] = {
            "title": title,
            "tags": json.loads(tags) if tags else [],
            "status": status,
            "created": created,
            "type": etype,
        }
    conn.close()
    return meta


def apply_filters(meta: dict, *, status, tags, etype, created_from, created_to) -> bool:
    if status and meta.get("status") != status:
        return False
    if etype and meta.get("type") != etype:
        return False
    if tags:
        have = set(meta.get("tags", []))
        if not set(tags).issubset(have):
            return False
    created = meta.get("created", "")
    if created_from and (not created or created < created_from):
        return False
    if created_to and (not created or created > created_to):
        return False
    return True


def make_snippet(path: Path, query: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    _, body = kbi._split_frontmatter(content)
    body = " ".join(body.split())
    lowered = body.lower()
    pos = -1
    for term in _ascii_terms(query) + _mecab_keywords(query):
        idx = lowered.find(term.lower())
        if idx != -1:
            pos = idx
            break
    if pos == -1:
        return body[:SNIPPET_CHARS] + ("…" if len(body) > SNIPPET_CHARS else "")
    start = max(0, pos - SNIPPET_CHARS // 3)
    end = min(len(body), start + SNIPPET_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return prefix + body[start:end] + suffix


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def search(root: Path, query: str, *, top: int, use_mecab: bool, lazy: bool,
           status, tags, etype, created_from, created_to) -> list[dict]:
    if lazy:
        stale = kbi.detect_stale(root)
        if stale:
            kbi.reindex(root, only=stale, verbose=False)

    lex = lexical_rank(root, query, use_mecab)
    vec = vector_rank(root, query)
    fused = rrf_fuse(lex, vec)

    # Expand see: from the current top before filtering.
    pre_top = [r for r, _ in sorted(fused.items(), key=lambda kv: -kv[1])][: top * 2]
    expand_see_one_hop(root, pre_top, fused)

    meta = _entry_meta(root)
    results = []
    for relpath, score in sorted(fused.items(), key=lambda kv: -kv[1]):
        m = meta.get(relpath, {})
        if not apply_filters(
            m, status=status, tags=tags, etype=etype,
            created_from=created_from, created_to=created_to,
        ):
            continue
        results.append(
            {
                "path": str(root / relpath),
                "relpath": relpath,
                "title": m.get("title", relpath),
                "score": round(score, 5),
                "status": m.get("status", ""),
                "tags": m.get("tags", []),
                "snippet": make_snippet(root / relpath, query),
            }
        )
        if len(results) >= top:
            break
    return results


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Hybrid search over a ccmemo knowledge base.")
    ap.add_argument("root", help="knowledge entries dir")
    ap.add_argument("query", help="search query")
    ap.add_argument("--status")
    ap.add_argument("--tag", action="append", default=[], dest="tags")
    ap.add_argument("--type", dest="etype")
    ap.add_argument("--created-from")
    ap.add_argument("--created-to")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--no-lazy", action="store_true")
    ap.add_argument("--no-mecab", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    results = search(
        root, args.query,
        top=args.top,
        use_mecab=not args.no_mecab,
        lazy=not args.no_lazy,
        status=args.status,
        tags=args.tags,
        etype=args.etype,
        created_from=args.created_from,
        created_to=args.created_to,
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        if not results:
            print("(no hits)")
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r['score']}] {r['title']}")
            print(f"   {r['relpath']}  ({r['status']}) {' '.join(r['tags'][:6])}")
            if r["snippet"]:
                print(f"   {r['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
