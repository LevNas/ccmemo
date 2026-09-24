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

After fusion, the top hits are expanded one hop along typed links (see / ref /
amends / extends — so a directly relevant entry pulls in its explicitly-linked
neighbours), then frontmatter filters (status / tag / type / created range) are
applied, and a ranked list of path + score + snippet is returned.

Neighbourhood summary mode (--summary) prints, per hit, the title, the
frontmatter `description` (the entry's trigger condition — when to open it) and
its typed edges with the one-line label each link carries, in the OKF
`index.md` shape (`* [Title](path) - description`). The point is to pick the
ONE entry worth opening from the summary alone: ten summaries cost less
context than one entry body.

Lazy refresh: before searching, on-disk sha256 hashes are compared with the index;
any entry that changed (or is new) is re-embedded just-in-time so results never go
stale. Disable with --no-lazy. From a linked git worktree the index of the main
checkout is used read-only: no refresh happens there, and a one-line warning
names the files the index is behind on.

Multi-corpus (1.28): with `scope: repo` in `.claude/ccmemo.json` the index
covers every document in the repository, each with a `kind` (`kb` for the
knowledge base, `docs` for the rest, or what the repository's `corpora`
rules declare). Every hit carries a `[kind]` badge; `--kind` filters. Hits
with identical content (same sha256) or the same entry `id` are folded into
one line that lists every location. Ranking never weights by kind.

Usage
-----
    uv run scripts/kb_search.py ROOT "クエリ" [filters...]
    python3 scripts/kb_search.py ROOT "クエリ" [filters...]

      ROOT                 knowledge entries dir (same arg as kb_index.py)
      --status active      only entries with this status
      --tag '#secret-management'   require this tag (repeatable)
      --type knowledge     only this frontmatter type
      --kind kb            only this corpus kind (repeatable; default: all)
      --created-from 2026-05-01    created >= date (YYYY-MM-DD)
      --created-to   2026-06-30    created <= date
      --top N              number of results (default 8)
      --summary            neighbourhood summary output (title / description / edges)
      --edges N            outgoing edges shown per hit (default 3, -1 = all)
      --linked-from N      incoming edges shown per hit (default 0, -1 = all)
      --no-lazy            skip search-time re-embedding of changed entries
      --no-mecab           do not run mecab tokenisation for the lexical arm
      --json               emit JSON instead of human-readable output

Depends on kb_index.py (imported) plus the same fastembed / sqlite-vec deps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Import the sibling indexer module regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_index as kbi  # noqa: E402
from lib import trust as _trust  # noqa: E402  (hooks/lib, on sys.path via kb_index)
from lib import config as _config  # noqa: E402

RRF_K = 60
SNIPPET_CHARS = 160
# Summary mode byte budget: ten summaries must cost less context than opening
# one entry body (~7 KB on a real corpus; Japanese text is 3 bytes per char).
# Measured on a 288-entry KB with these caps: 10 hits ≈ 6.9 KB (incoming
# edges off; each incoming edge line adds ~90 bytes per hit).
SUMMARY_TITLE_CHARS = 100       # hit title
SUMMARY_DESC_CHARS = 80         # description / lead-paragraph fallback
SUMMARY_LABEL_CHARS = 40        # edge label
SUMMARY_EDGE_TITLE_CHARS = 24   # neighbour title, only when the edge has no label
DEFAULT_EDGES = 3
DEFAULT_LINKED_FROM = 0


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


def lexical_rank(root: Path, query: str, use_mecab: bool,
                 ctx: "kbi.RootContext | None" = None) -> list[str]:
    """Return entry relpaths ranked by lexical relevance (best first).

    Scoring mirrors the existing hook: each matching term contributes 1/hit_count
    to every file it matched, so rare terms weigh more. Terms matching too many
    files are skipped as non-discriminating.

    ripgrep runs over the key base (the knowledge root, or the repository
    root in `scope: repo`) and only files in the index's candidate set count,
    so an excluded capture or an ignored file never leaks into the ranking.
    Files over the embedding size cap are still found here: rg reads them.
    """
    if not shutil.which("rg"):
        return []
    ctx = ctx or kbi.context(root)
    base = ctx.key_base
    allowed = {key for _p, key in kbi.candidate_files(root, ctx)}
    rg_opts: list[str] = []
    if ctx.scope == "repo":
        # The knowledge base lives under a dotdir; rg skips hidden paths by
        # default. `.git` stays out; everything else is filtered by `allowed`.
        rg_opts = ["--hidden", "--glob", "!.git/"]

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
                ["rg", "-l", "--fixed-strings", "--ignore-case", *rg_opts, "--", term, str(base)],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        files: list[str] = []
        for ln in res.stdout.splitlines():
            if not ln.strip():
                continue
            try:
                rel = Path(ln).resolve().relative_to(base).as_posix()
            except ValueError:
                continue
            if rel in allowed:
                files.append(rel)
        n = len(files)
        if n == 0 or n > hit_count_limit:
            continue
        # rg prints hits in thread-completion order, which varies from run
        # to run; sorting them fixes the order of equal scores (and so the
        # RRF ranks) so the same query gives the same list every time.
        for rel in sorted(files):
            scores[rel] = scores.get(rel, 0.0) + 1.0 / n

    return [rel for rel, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


# --------------------------------------------------------------------------- #
# Vector arm (sqlite-vec KNN)
# --------------------------------------------------------------------------- #

def vector_rank(root: Path, query: str, k: int = 40) -> list[str]:
    """Return entry relpaths ranked by best (closest) chunk distance, best first."""
    conn = kbi.open_index(root)
    if conn is None:
        return []
    qvec = kbi.embed_query(query)
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

    Follows every typed edge (see / ref / amends / extends) stored in the
    `edges` table. A neighbour reachable from a top hit gets a small boost
    (half the linking entry's score) so it can surface even if neither arm
    ranked it directly.
    """
    conn = kbi.open_index(root)
    if conn is None:
        return
    known = {row[0] for row in conn.execute("SELECT relpath FROM entries")}
    for relpath in top:
        for (nb,) in conn.execute(
            "SELECT target FROM edges WHERE src = ? ORDER BY ord", (relpath,)
        ):
            if nb in known and nb not in fused:
                fused[nb] = 0.5 * fused.get(relpath, 0.0)
    conn.close()


def _entry_meta(root: Path) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    conn = kbi.open_index(root)
    if conn is None:
        return meta
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
    # A read-only (shared) index may predate schema 5: read what it has.
    kind_sql = "kind" if "kind" in cols else "''"
    emb_sql = "embedded" if "embedded" in cols else "1"
    for (relpath, title, tags, status, created, etype, description, handle,
         eid, gen_by, gen_at, tier, tier_at, sha, kind, embedded) in conn.execute(
        "SELECT relpath, title, tags, status, created, type, description, handle, "
        f"id, generated_by, generated_at, verified_tier, verified_at, sha256, {kind_sql}, "
        f"{emb_sql} FROM entries"
    ):
        meta[relpath] = {
            "title": title,
            "handle": handle or entry_id(relpath),
            "id": eid or "",
            "generated": {"by": gen_by, "at": gen_at} if gen_by else None,
            "verified_tier": tier or "",
            "verified_at": tier_at or "",
            "tags": json.loads(tags) if tags else [],
            "status": status,
            "created": created,
            "type": etype,
            "description": description or "",
            "sha256": sha or "",
            "kind": kind or "kb",
            "embedded": bool(embedded),
        }
    conn.close()
    return meta


def _cap(rows: list, n: int) -> list:
    return rows if n < 0 else rows[:n]


def entry_edges(root: Path, relpaths: list[str], meta: dict[str, dict], *,
                max_out: int, max_in: int) -> dict[str, dict]:
    """Typed edges around each relpath, both directions, from the index only.

    Returns {relpath: {"edges": [...], "edges_total": n,
                       "linked_from": [...], "linked_from_total": m}}.
    Each edge carries the neighbour's title and short handle (from the index)
    so the caller can render it without opening any file.
    """
    out: dict[str, dict] = {}
    conn = kbi.open_index(root)
    if conn is None:
        return out
    for rp in relpaths:
        outgoing = [
            {"target": t, "rel": r, "label": lb or "",
             "title": meta.get(t, {}).get("title", ""),
             "handle": meta.get(t, {}).get("handle") or entry_id(t)}
            for t, r, lb in conn.execute(
                "SELECT target, rel, label FROM edges WHERE src = ? ORDER BY ord", (rp,)
            )
        ]
        incoming = [
            {"source": sr, "rel": r, "label": lb or "",
             "title": meta.get(sr, {}).get("title", ""),
             "handle": meta.get(sr, {}).get("handle") or entry_id(sr)}
            for sr, r, lb in conn.execute(
                "SELECT src, rel, label FROM edges WHERE target = ? ORDER BY src DESC", (rp,)
            )
        ]
        out[rp] = {
            "edges": _cap(outgoing, max_out),
            "edges_total": len(outgoing),
            "linked_from": _cap(incoming, max_in),
            "linked_from_total": len(incoming),
        }
    conn.close()
    return out


def apply_filters(meta: dict, *, status, tags, etype, created_from, created_to,
                  verified_min: str = "", kinds=None, conventions: str = "kb") -> bool:
    """Frontmatter filters. `kinds` restricts the corpus kind (None = all).
    `conventions` is the vocabulary the hit's corpus writes: a `plain`
    corpus's `status: current` satisfies `--status active`."""
    if kinds and meta.get("kind", "kb") not in kinds:
        return False
    if status and (_config.normalize_status(meta.get("status", ""), conventions)
                   != _config.normalize_status(status, conventions)):
        return False
    if verified_min and not _trust.tier_at_least(meta.get("verified_tier", ""), verified_min):
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


_HEADING_RE = re.compile(r"^\s*#{1,6}\s")


def make_lead(path: Path, max_chars: int = SUMMARY_DESC_CHARS * 2) -> str:
    """First body paragraph that is not a heading — the fallback for an entry
    without a frontmatter `description` (entries written before the field
    existed). Truncated by the caller; kept generous here for JSON consumers."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    _, body = kbi._split_frontmatter(content)
    for para in re.split(r"\n\s*\n", body):
        lines = [ln for ln in para.splitlines() if ln.strip()]
        if not lines or _HEADING_RE.match(lines[0]):
            continue
        text = " ".join(" ".join(lines).split())
        if text.startswith(("- ", "* ", "|", "```", ">")):
            continue  # lists / tables / fences / quotes are not a lead
        return text[:max_chars]
    return ""


def make_snippet(path: Path, query: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    _, body = kbi._split_frontmatter(content)  # shared parser via kb_index
    # The H1 repeats the title; drop it so the snippet adds information.
    body = re.sub(r"^\s*#\s[^\n]*\n", "", body, count=1)
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

def _fold_key(relpath: str, m: dict) -> tuple[str, str]:
    """Hits with the same content (sha256) or the same entry `id` are one
    result: the first-ranked location stands for all of them."""
    if m.get("id"):
        return ("id", m["id"])
    if m.get("sha256"):
        return ("sha", m["sha256"])
    return ("path", relpath)


def search(root: Path, query: str, *, top: int, use_mecab: bool, lazy: bool,
           status, tags, etype, created_from, created_to,
           max_edges: int = DEFAULT_EDGES,
           max_linked_from: int = DEFAULT_LINKED_FROM,
           verified_min: str = "", kinds=None) -> list[dict]:
    ctx = kbi.context(root)
    base = ctx.key_base
    if lazy:
        if ctx.shared:
            # A linked worktree reads the main checkout's index and never
            # writes it: say how far behind it is instead of refreshing.
            stale = kbi.detect_stale(root)
            if stale:
                print(f"ccmemo search: shared index {ctx.db_path} is behind on "
                      f"{len(stale)} file(s) in this worktree (refresh from the main "
                      f"checkout, or set {kbi.INDEX_ENV})", file=sys.stderr)
        else:
            stale = kbi.detect_stale(root)
            if stale:
                kbi.reindex(root, only=stale, verbose=False)
            else:
                # An index built before the edges table existed: backfill the
                # derived columns from the Markdown (no re-embedding).
                kbi.ensure_metadata(root)

    lex = lexical_rank(root, query, use_mecab, ctx)
    vec = vector_rank(root, query)
    fused = rrf_fuse(lex, vec)

    # Expand see: from the current top before filtering.
    pre_top = [r for r, _ in sorted(fused.items(), key=lambda kv: -kv[1])][: top * 2]
    expand_see_one_hop(root, pre_top, fused)

    meta = _entry_meta(root)
    # Folding: same id or same content -> one line, every location listed.
    # Folded twins must be equivalent under the filters, so filter first.
    results = []
    folded: dict[tuple[str, str], dict] = {}
    for relpath, score in sorted(fused.items(), key=lambda kv: -kv[1]):
        m = meta.get(relpath, {})
        kind = m.get("kind", "kb")
        if not apply_filters(
            m, status=status, tags=tags, etype=etype,
            created_from=created_from, created_to=created_to,
            verified_min=verified_min, kinds=kinds,
            conventions=ctx.cfg.conventions_of(kind),
        ):
            continue
        fk = _fold_key(relpath, m)
        head = folded.get(fk)
        if head is not None:
            head["locations"].append(relpath)
            if m.get("sha256") != head.get("sha256"):
                head["divergent"] = True
            continue
        if len(results) >= top:
            continue  # keep folding into shown hits; do not open new ones
        description = m.get("description", "")
        source = "frontmatter" if description else "lead"
        if not description:
            description = make_lead(base / relpath)
        r = {
            "path": str(base / relpath),
            "relpath": relpath,
            "kind": kind,
            "handle": m.get("handle") or entry_id(relpath),
            "id": m.get("id", ""),
            "title": m.get("title", relpath),
            "score": round(score, 5),
            "status": m.get("status", ""),
            "tags": m.get("tags", []),
            "description": description,
            "description_source": source,
            "generated": m.get("generated"),
            "verified_tier": m.get("verified_tier", ""),
            "verified_at": m.get("verified_at", ""),
            "sha256": m.get("sha256", ""),
            "embedded": m.get("embedded", True),
            "locations": [],
            "divergent": False,
            "snippet": make_snippet(base / relpath, query),
        }
        folded[fk] = r
        results.append(r)

    edge_info = entry_edges(
        root, [r["relpath"] for r in results], meta,
        max_out=max_edges, max_in=max_linked_from,
    )
    for r in results:
        r.update(edge_info.get(r["relpath"], {
            "edges": [], "edges_total": 0, "linked_from": [], "linked_from_total": 0,
        }))
    return results


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def _trunc(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


_ENTRY_ID_RE = re.compile(r"^(\d{8}-\d{6})-")


def entry_id(relpath: str) -> str:
    """Fallback short name: the `YYYYMMDD-HHMMSS` filename prefix.

    Used only when the index has no `handle` for the path (an entry not yet
    indexed). The prefix is usually unique but not by construction —
    entries written in the same second share it — so the index computes
    the shortest corpus-unique handle (`kb_index.assign_handles`) and the
    summary prints that. Files without the prefix fall back to the basename.
    """
    name = os.path.basename(relpath)
    m = _ENTRY_ID_RE.match(name)
    return m.group(1) if m else name


def _kind_badge(r: dict) -> str:
    """` [kind]` after the title; nothing when the hit carries no kind (an
    index that predates 1.28, or a caller-built dict)."""
    return f" [{r['kind']}]" if r.get("kind") else ""


def _locations_line(r: dict, indent: str) -> str:
    """`also: path, path` for a folded hit (same content or same id)."""
    locs = r.get("locations") or []
    if not locs:
        return ""
    note = " (divergent: same id, different content)" if r.get("divergent") else ""
    return f"{indent}also: {', '.join(locs)}{note}"


def _edge_line(e: dict, key: str, arrow: str) -> str:
    ref = e.get("handle") or entry_id(e[key])
    if e.get("label"):
        tail = _trunc(e["label"], SUMMARY_LABEL_CHARS)
    else:
        tail = _trunc(e.get("title") or e[key], SUMMARY_EDGE_TITLE_CHARS)
    return f"  - {arrow}{e['rel']} {ref} — {tail}"


def format_summary(results: list[dict]) -> str:
    """Neighbourhood summary in the OKF `index.md` shape.

    One top-level bullet per hit — `* [Title](relpath) - description` — with
    the typed edges nested under it as `- rel <handle> — label` (`←` marks an
    incoming link; `(+N)` on the last line counts edges not shown). The label
    is what the linking author wrote after the link, i.e. the reason to
    follow it, so neighbours are identified by that reason plus a short
    handle — the date-time prefix, or the filename when two entries share
    one — rather than by their (long) title. The description falls back to the
    entry's lead paragraph, marked `(lead)`, when the entry has none (entries
    written before the field existed). Non-active status is flagged inline so
    a superseded hit is never picked by mistake. A verified hit carries its
    trust tier as one word — `[human]` or `[machine]` — after the title;
    unverified hits carry nothing (the tier never affects ranking).
    """
    if not results:
        return "(no hits)"
    lines: list[str] = []
    for r in results:
        desc = r.get("description") or r.get("snippet") or ""
        if r.get("description_source", "frontmatter") != "frontmatter" and desc:
            desc = "(lead) " + desc
        flag = "" if r.get("status") in ("", "active") else f" ({r['status']})"
        tier = f" [{r['verified_tier']}]" if r.get("verified_tier") else ""
        lines.append(f"* [{_trunc(r['title'], SUMMARY_TITLE_CHARS)}]({r['relpath']})"
                     f"{_kind_badge(r)}{flag}{tier} - {_trunc(desc, SUMMARY_DESC_CHARS)}")
        also = _locations_line(r, "  ")
        if also:
            lines.append(also)
        shown = r.get("edges", [])
        for e in shown:
            lines.append(_edge_line(e, "target", ""))
        more = r.get("edges_total", 0) - len(shown)
        if more > 0 and shown:
            lines[-1] += f" (+{more})"
        elif more > 0:
            lines.append(f"  - (+{more} outgoing)")
        shown_in = r.get("linked_from", [])
        for e in shown_in:
            lines.append(_edge_line(e, "source", "← "))
        more_in = r.get("linked_from_total", 0) - len(shown_in)
        if more_in > 0 and shown_in:
            lines[-1] += f" (+{more_in})"
        elif more_in > 0:
            lines.append(f"  - (← +{more_in} incoming)")
    return "\n".join(lines)


def format_ranked(results: list[dict]) -> str:
    if not results:
        return "(no hits)"
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        tier = f" [{r['verified_tier']}]" if r.get("verified_tier") else ""
        lines.append(f"{i}. [{r['score']}] {r['title']}{_kind_badge(r)}{tier}")
        lines.append(f"   {r['relpath']}  ({r['status']}) {' '.join(r['tags'][:6])}")
        also = _locations_line(r, "   ")
        if also:
            lines.append(also)
        if r.get("description") and r.get("description_source", "frontmatter") == "frontmatter":
            lines.append(f"   when: {_trunc(r['description'], SUMMARY_DESC_CHARS)}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Hybrid search over a ccmemo knowledge base.")
    ap.add_argument("root", help="knowledge entries dir")
    ap.add_argument("query", help="search query")
    ap.add_argument("--status")
    ap.add_argument("--tag", action="append", default=[], dest="tags")
    ap.add_argument("--type", dest="etype")
    ap.add_argument("--kind", action="append", default=[], dest="kinds",
                    help="only hits of this corpus kind, e.g. kb or docs (repeatable; "
                         "default: every kind)")
    ap.add_argument("--created-from")
    ap.add_argument("--created-to")
    ap.add_argument("--verified", choices=["machine", "human"], default="", dest="verified_min",
                    help="only hits verified at least at this tier (machine: an agent or a "
                         "process checked it; human: a person did); never affects ranking")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--summary", action="store_true",
                    help="neighbourhood summary: title / description / typed edges per hit")
    ap.add_argument("--edges", type=int, default=DEFAULT_EDGES,
                    help=f"outgoing edges kept per hit (default {DEFAULT_EDGES}, -1 = all)")
    ap.add_argument("--linked-from", type=int, default=DEFAULT_LINKED_FROM, dest="linked_from",
                    help=f"incoming edges kept per hit (default {DEFAULT_LINKED_FROM}, -1 = all)")
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
        max_edges=args.edges,
        max_linked_from=args.linked_from,
        verified_min=args.verified_min,
        kinds=args.kinds or None,
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.summary:
        print(format_summary(results))
    else:
        print(format_ranked(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
