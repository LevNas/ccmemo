#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastembed>=0.3",
#     "sqlite-vec>=0.1.6",
# ]
# ///
"""Replay logged search misses against kb_search and report hit@N.

Retrieval misses have several independent causes (vocabulary gap, frequent
terms dropped by the lexical arm, embedding dilution on long entries, RRF
cut-off, missing metadata). Which one dominates on *your* corpus cannot be
guessed, so the plugin asks you to keep a log of misses as (query, expected
entry) pairs and replays it before and after any retrieval change. Compare
two runs with --compare.

Log format (a Markdown table anywhere in the file; other lines are ignored)::

    | date | query | expected | actual top | cause |
    |---|---|---|---|---|
    | 2026-09-23 | see リンクを frontmatter に移すべきか | 20260923-143000 | ... | vocabulary |

`expected` is a unique filename substring or an entries-root relpath; several
alternatives may be separated by `;` (any one hitting counts). JSON Lines
(`{"query": ..., "expected": [...]}` per line) is accepted too. Keep the log
outside the entries directory so it is never indexed or searched itself.

Usage::

    uv run scripts/kb_recall_eval.py ROOT MISSES.md [--top 10] [--json]
    uv run scripts/kb_recall_eval.py ROOT MISSES.md --json > after.json
    uv run scripts/kb_recall_eval.py ROOT MISSES.md --compare before.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_search as ks  # noqa: E402


def load_pairs(path: Path) -> list[dict]:
    """(query, expected[]) pairs from a Markdown table or JSON Lines."""
    pairs: list[dict] = []
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            exp = obj.get("expected", [])
            exp = [exp] if isinstance(exp, str) else list(exp)
            if obj.get("query") and exp:
                pairs.append({"query": obj["query"], "expected": exp})
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue  # separator row
        query, expected = cells[1], cells[2]
        if query.lower() in ("query", "質問", "投げた質問"):
            continue  # header row
        exp = [e.strip().strip("`") for e in expected.replace("<br>", ";").split(";") if e.strip()]
        if query and exp:
            pairs.append({"query": query, "expected": exp})
    return pairs


def rank_of(results: list[dict], expected: list[str]) -> int:
    for i, r in enumerate(results, 1):
        if any(e in r["relpath"] for e in expected):
            return i
    return 0


def evaluate(root: Path, pairs: list[dict], *, top: int, lazy: bool) -> list[dict]:
    out = []
    for p in pairs:
        results = ks.search(
            root, p["query"], top=top, use_mecab=True, lazy=lazy,
            status=None, tags=[], etype=None, created_from=None, created_to=None,
            max_edges=0, max_linked_from=0,
        )
        lazy = False  # one refresh is enough
        out.append({
            "query": p["query"],
            "expected": p["expected"],
            "rank": rank_of(results, p["expected"]),
            "top": [r["relpath"] for r in results[:3]],
        })
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Replay logged search misses; report hit@N.")
    ap.add_argument("root", help="knowledge entries dir")
    ap.add_argument("misses", help="misses log (Markdown table or JSON Lines)")
    ap.add_argument("--top", type=int, default=10, help="N for hit@N (default 10)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--compare", metavar="PREV.json", help="earlier --json output to diff against")
    ap.add_argument("--no-lazy", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    pairs = load_pairs(Path(args.misses).expanduser())
    if not pairs:
        print("error: no (query, expected) pairs found in the log", file=sys.stderr)
        return 2
    rows = evaluate(root, pairs, top=args.top, lazy=not args.no_lazy)
    hits = sum(1 for r in rows if r["rank"])
    report = {"top": args.top, "pairs": len(rows), "hits": hits,
              "hit_rate": round(hits / len(rows), 3), "rows": rows}

    prev = {}
    if args.compare:
        with open(args.compare, encoding="utf-8") as f:
            prev = {r["query"]: r["rank"] for r in json.load(f).get("rows", [])}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for r in rows:
        mark = f"#{r['rank']}" if r["rank"] else "miss"
        delta = ""
        if r["query"] in prev:
            before = prev[r["query"]]
            if before != r["rank"]:
                delta = f"  (was {'#' + str(before) if before else 'miss'})"
        print(f"{mark:>5}  {r['query']}{delta}")
        if not r["rank"]:
            print(f"       expected {r['expected']}; top: {', '.join(r['top'])}")
    print(f"\nhit@{args.top}: {hits}/{len(rows)} ({report['hit_rate']:.0%})")
    if prev:
        prev_hits = sum(1 for q in prev if prev[q])
        print(f"previous: {prev_hits}/{len(prev)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
