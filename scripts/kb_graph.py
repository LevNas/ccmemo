#!/usr/bin/env python3
"""Link-graph CLI over a ccmemo knowledge base.

Builds the entry graph on demand from `- see:` / `- ref:` list links
(no persisted index; ~1s for a few hundred entries). Pure stdlib —
runs with plain python3, no uv, no vector index, no network.

Subcommands:
  stats                  graph overview: hubs, orphans, components
  neighborhood <entry>   BFS neighborhood (structure only, no body text)
  path <a> <b>           shortest link path between two entries
  lineage <entry>        supersede chain: what this replaced, what replaced it
  link-add <src> <dst>   deterministically append a typed link line to src
  supersede <old> <new>  mark old as replaced by new: frontmatter pair,
                         body-top banner, amends back-link — in one atomic step
  lint [files...]        deterministic checks (pre-commit friendly, exit 1 on findings)

Edge kinds:
  see / ref              untyped association (list links, unchanged)
  amends / extends       typed list links: correction note / elaboration
  superseded_by          derived from the `superseded_by:` frontmatter field
                         (change flow) — old entry -> replacement, max one
                         per entry, entries-root-relative path only

Output contains entry IDs, titles and edge types only — never body text —
so results stay cheap to inject into a model context. The intended flow is
structure first, bodies last: use neighborhood/path to plan where to go,
then read only the endpoint entries.

lint is deterministic by design (no model calls) so it can gate commits;
judgment work such as staleness review stays in /review-knowledge.

Usage (from the project root):
    python3 scripts/kb_graph.py stats
    python3 scripts/kb_graph.py neighborhood <partial-name> --depth 2
    python3 scripts/kb_graph.py path <partial-name-a> <partial-name-b>
    python3 scripts/kb_graph.py link-add <partial-a> <partial-b> \
        --reason "relationship" --bidirectional
    python3 scripts/kb_graph.py lint [changed-files...]

link-add is the writer counterpart of the graph reader: the model decides
WHICH entries to connect and writes the reason; the mechanical edit is
deterministic. It appends after the entry's last see:/ref: line (or after a
`## 関連` heading), is idempotent per target, writes atomically, and exits
non-zero on any ambiguity so the caller can fall back to a manual edit.
With --bidirectional both directions are validated before either file is
written (no partial application).

Entries are addressed by unique filename substring. `--json` gives
machine-readable output. `--root` points at the entries dir
(default: .claude/knowledge/entries).

pre-commit example (fires only when staged entries changed):
    changed=$(git diff --cached --name-only -- .claude/knowledge/entries/)
    [ -z "$changed" ] || python3 scripts/kb_graph.py lint $changed
(inside this repo only — a CONSUMING repo must not point a hook at the
version-keyed plugin cache path; see docs/link-graph.md for the wiring)
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from collections import deque

LINK_RE = re.compile(r"^\s*-\s+(see|ref|amends|extends):\s*\[([^\]]*)\]\(([^)]+)\)", re.MULTILINE)
# Loose shape of a link line: anything matching this but not LINK_RE would be
# silently dropped from the graph (e.g. a label containing a square bracket),
# so lint reports it as malformed-link instead of staying quiet.
LOOSE_LINK_RE = re.compile(r"^\s*-\s+(see|ref|amends|extends):\s*\[")
# Both registry line forms in use: "- #tag — description (count)" and "`#tag`"
TAG_REGISTRY_RES = (
    re.compile(r"^- (#[\w\-]+)", re.MULTILINE),
    re.compile(r"^`(#[\w\-]+)`$", re.MULTILINE),
)
FILENAME_RE = re.compile(r"^\d{8}-\d{6}-.+\.md$")


def parse_frontmatter(text):
    meta = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    for line in text[3:end].splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"')
    return meta


def load_graph(root):
    """Return (nodes, edges, problems).

    nodes: {id: {"title", "tags", "status"}} — id is path relative to entries root
    edges: [(src, dst, kind, resolution)] — resolution: "root" | "fallback"
    problems: lint findings collected during parsing
    """
    root = os.path.abspath(root)
    nodes, edges, problems = {}, [], []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if not fn.endswith(".md") or fn == "CLAUDE.md":
                continue
            path = os.path.join(dirpath, fn)
            nid = os.path.relpath(path, root)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            meta = parse_frontmatter(text)
            nodes[nid] = {
                "title": meta.get("title", ""),
                "tags": set(re.findall(r"#[\w\-]+", meta.get("tags", ""))),
                "status": meta.get("status", ""),
            }
            if not meta.get("title"):
                problems.append((nid, "missing-title", "no frontmatter title"))
            if not FILENAME_RE.match(fn):
                problems.append((nid, "filename", "does not match <date>-<time>-...-<slug>.md"))
            for lineno, line in enumerate(text.splitlines(), 1):
                if LOOSE_LINK_RE.match(line) and not LINK_RE.match(line):
                    problems.append((nid, "malformed-link",
                                     f"line {lineno}: does not parse as a link, "
                                     f"no edge produced: {line.strip()[:80]}"))
            sup = meta.get("superseded_by", "")
            status = meta.get("status", "")
            if sup:
                if status != "superseded":
                    problems.append((nid, "superseded-status-mismatch",
                                     f"superseded_by present but status is '{status or '(none)'}'"))
                t = sup.split("#")[0].strip()
                # spec says entries-root-relative — deliberately no dirpath fallback
                cand = os.path.normpath(os.path.join(root, t))
                if os.path.exists(cand) and cand.startswith(root + os.sep):
                    dst = os.path.relpath(cand, root)
                    if dst == nid:
                        problems.append((nid, "self-link", "superseded_by: links to itself"))
                    else:
                        edges.append((nid, dst, "superseded_by", "frontmatter"))
                else:
                    problems.append((nid, "superseded-broken",
                                     f"superseded_by: ({sup}) resolves to no entry"))
            elif status == "superseded":
                problems.append((nid, "superseded-missing-successor",
                                 "status: superseded but no superseded_by"))
            for kind, _label, target in LINK_RE.findall(text):
                t = target.split("#")[0].strip()
                if not t or t.startswith(("http://", "https://")):
                    continue
                cand_root = os.path.normpath(os.path.join(root, t))
                cand_file = os.path.normpath(os.path.join(dirpath, t))
                if os.path.exists(cand_root):
                    resolved, resolution = cand_root, "root"
                elif os.path.exists(cand_file):
                    resolved, resolution = cand_file, "fallback"
                else:
                    # targets escaping the repository resolve differently per
                    # checkout location (worktrees, other machines) — report
                    # them separately from links broken inside the repo.
                    # assumes root is <repo>/.claude/knowledge/entries
                    repo_root = os.path.normpath(os.path.join(root, "..", "..", ".."))
                    in_repo = (cand_root.startswith(repo_root + os.sep)
                               or cand_file.startswith(repo_root + os.sep))
                    check = "broken-link" if in_repo else "out-of-tree"
                    problems.append((nid, check, f"{kind}: ({t}) resolves to no file"))
                    continue
                if resolved.startswith(root + os.sep):
                    dst = os.path.relpath(resolved, root)
                    if dst == nid:
                        problems.append((nid, "self-link", f"{kind}: links to itself"))
                        continue
                    edges.append((nid, dst, kind, resolution))
                # links leaving the entries tree (rules, docs...) are checked
                # for existence above but are not part of the entry graph
    seen = set()
    deduped = []
    for e in edges:
        key = e[:3]
        if key in seen:
            problems.append((e[0], "duplicate-link", f"{e[2]}: ({e[1]}) listed more than once"))
            continue
        seen.add(key)
        deduped.append(e)
    return nodes, deduped, problems


def adjacency(nodes, edges):
    out_adj = {n: [] for n in nodes}
    in_adj = {n: [] for n in nodes}
    for src, dst, kind, _res in edges:
        if src in out_adj and dst in in_adj:
            out_adj[src].append((dst, kind))
            in_adj[dst].append((src, kind))
    return out_adj, in_adj


def components(nodes, edges):
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for src, dst, _kind, _res in edges:
        if src in parent and dst in parent:
            parent[find(src)] = find(dst)
    comps = {}
    for n in nodes:
        comps.setdefault(find(n), []).append(n)
    return sorted(comps.values(), key=len, reverse=True)


def resolve_entry(nodes, query):
    if query in nodes:
        return query
    matches = [n for n in nodes if query in n]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"error: no entry matches '{query}'")
    sys.exit("error: ambiguous query '%s' (%d matches):\n  %s"
             % (query, len(matches), "\n  ".join(sorted(matches)[:10])))


def short(title, width=70):
    return title if len(title) <= width else title[: width - 1] + "…"


def cmd_stats(nodes, edges, as_json):
    out_adj, in_adj = adjacency(nodes, edges)
    degree = {n: len(out_adj[n]) + len(in_adj[n]) for n in nodes}
    hubs = sorted(nodes, key=lambda n: degree[n], reverse=True)[:10]
    orphans = sorted(n for n in nodes if degree[n] == 0)
    comps = components(nodes, edges)
    kinds = {}
    for _s, _d, k, _r in edges:
        kinds[k] = kinds.get(k, 0) + 1
    if as_json:
        print(json.dumps({
            "nodes": len(nodes), "edges": len(edges), "edge_kinds": kinds,
            "hubs": [{"id": n, "in": len(in_adj[n]), "out": len(out_adj[n]),
                      "title": nodes[n]["title"]} for n in hubs],
            "orphans": orphans,
            "components": [len(c) for c in comps],
        }, ensure_ascii=False, indent=1))
        return
    print(f"nodes: {len(nodes)}  edges: {len(edges)}  ({', '.join(f'{k}={v}' for k, v in sorted(kinds.items()))})")
    print(f"components: {len(comps)}  sizes: {[len(c) for c in comps[:8]]}"
          + (" ..." if len(comps) > 8 else ""))
    print("\ntop hubs (in/out):")
    for n in hubs:
        print(f"  {len(in_adj[n]):3d}/{len(out_adj[n]):<3d} {n}")
        print(f"          {short(nodes[n]['title'])}")
    print(f"\norphans (no links in either direction): {len(orphans)}")
    for n in orphans:
        print(f"  {n}  {short(nodes[n]['title'], 50)}")


def cmd_neighborhood(nodes, edges, start, depth, as_json):
    out_adj, in_adj = adjacency(nodes, edges)
    visited = {start: 0}
    rows = []
    q = deque([start])
    while q:
        cur = q.popleft()
        if visited[cur] >= depth:
            continue
        neigh = [(dst, kind, "→") for dst, kind in out_adj[cur]] + \
                [(src, kind, "←") for src, kind in in_adj[cur]]
        for other, kind, direction in neigh:
            if other not in visited:
                visited[other] = visited[cur] + 1
                rows.append((visited[other], cur, direction, kind, other))
                q.append(other)
    if as_json:
        print(json.dumps({"start": start, "depth": depth,
                          "neighbors": [{"depth": d, "from": c, "dir": dr, "kind": k,
                                         "id": o, "title": nodes[o]["title"]}
                                        for d, c, dr, k, o in rows]},
                         ensure_ascii=False, indent=1))
        return
    print(f"{start}\n  {short(nodes[start]['title'])}\n")
    for d, _cur, direction, kind, other in rows:
        print(f"  d{d} {direction}{kind:<4} {other}")
        print(f"           {short(nodes[other]['title'])}")
    print(f"\n{len(rows)} entries within depth {depth}")


def cmd_path(nodes, edges, a, b, as_json):
    out_adj, in_adj = adjacency(nodes, edges)
    prev = {a: None}
    q = deque([a])
    while q and b not in prev:
        cur = q.popleft()
        neigh = [(dst, kind, "→") for dst, kind in out_adj[cur]] + \
                [(src, kind, "←") for src, kind in in_adj[cur]]
        for other, kind, direction in neigh:
            if other not in prev:
                prev[other] = (cur, kind, direction)
                q.append(other)
    if b not in prev:
        print(f"no path between\n  {a}\n  {b}")
        sys.exit(1)
    chain = []
    cur = b
    while prev[cur]:
        parent, kind, direction = prev[cur]
        chain.append((parent, direction, kind, cur))
        cur = parent
    chain.reverse()
    if as_json:
        print(json.dumps({"hops": len(chain),
                          "path": [{"from": p, "dir": d, "kind": k, "to": t}
                                   for p, d, k, t in chain]}, ensure_ascii=False, indent=1))
        return
    print(f"{a}\n  {short(nodes[a]['title'])}")
    for _parent, direction, kind, target in chain:
        print(f"    {direction}{kind}")
        print(f"{target}\n  {short(nodes[target]['title'])}")
    print(f"\n{len(chain)} hops")


def supersede_maps(edges):
    nxt, prevs = {}, {}
    for s, d, k, _r in edges:
        if k == "superseded_by":
            nxt[s] = d  # max one superseded_by per entry -> chain structure
            prevs.setdefault(d, []).append(s)
    return nxt, prevs


def supersede_cycles(edges):
    nxt, _prevs = supersede_maps(edges)
    cycles, done = [], set()
    for start in nxt:
        if start in done:
            continue
        order = {}
        cur = start
        while cur in nxt and cur not in order and cur not in done:
            order[cur] = len(order)
            cur = nxt[cur]
        if cur in order:
            chain = sorted(order, key=order.get)
            cycles.append(chain[order[cur]:])
        done.update(order)
    return cycles


def cmd_lineage(nodes, edges, start, as_json):
    nxt, prevs = supersede_maps(edges)
    ancestors = []  # entries this one (transitively) replaced
    q = deque([start])
    seen = {start}
    while q:
        for p in sorted(prevs.get(q.popleft(), [])):
            if p not in seen:
                seen.add(p)
                ancestors.append(p)
                q.append(p)
    successors = []  # replacement chain from this entry forward
    cur = start
    while cur in nxt and nxt[cur] not in successors and nxt[cur] != start:
        cur = nxt[cur]
        successors.append(cur)
    current = successors[-1] if successors else start
    if as_json:
        print(json.dumps({
            "entry": start,
            "ancestors": [{"id": n, "title": nodes[n]["title"]} for n in ancestors],
            "successors": [{"id": n, "title": nodes[n]["title"]} for n in successors],
            "current": {"id": current, "title": nodes[current]["title"],
                        "status": nodes[current]["status"]},
        }, ensure_ascii=False, indent=1))
        return
    print(f"{start}\n  {short(nodes[start]['title'])}")
    if ancestors:
        print("\nreplaces (transitively):")
        for n in ancestors:
            print(f"  ← {n}\n      {short(nodes[n]['title'])}")
    if successors:
        print("\nreplaced by (chain):")
        for n in successors:
            print(f"  → {n}\n      {short(nodes[n]['title'])}")
    flag = "" if nodes[current]["status"] in ("active", "draft") else f"  [status: {nodes[current]['status']}]"
    print(f"\ncurrent authority: {current}{flag}")


def cmd_lint(nodes, edges, problems, registry_path, only_files, as_json):
    findings = list(problems)
    for cyc in supersede_cycles(edges):
        for nid in cyc:  # one finding per member so the only_files filter still hits
            findings.append((nid, "supersede-cycle", " → ".join(cyc + [cyc[0]])))
    if registry_path and os.path.isfile(registry_path):
        with open(registry_path, encoding="utf-8") as f:
            registry_text = f.read()
        registry = set()
        for pat in TAG_REGISTRY_RES:
            registry.update(pat.findall(registry_text))
        for nid, info in nodes.items():
            unknown = info["tags"] - registry
            if unknown:
                findings.append((nid, "unknown-tag",
                                 "not in registry: " + " ".join(sorted(unknown))))
    if only_files:
        keys = {os.path.basename(f) for f in only_files}
        findings = [f for f in findings if os.path.basename(f[0]) in keys]
    findings.sort()
    if as_json:
        print(json.dumps([{"id": i, "check": c, "detail": d} for i, c, d in findings],
                         ensure_ascii=False, indent=1))
    else:
        for nid, check, detail in findings:
            print(f"{check:>14}  {nid}\n                {detail}")
        print(f"\n{len(findings)} finding(s)")
    sys.exit(1 if findings else 0)


SEE_SECTION_RE = re.compile(r"^##\s*(関連|Related)\s*$", re.MULTILINE)


def _resolved_link_targets(text, root, dirpath):
    """Resolve every see/ref target in text to an entries-root-relative id."""
    targets = set()
    for _kind, _label, target in LINK_RE.findall(text):
        t = target.split("#")[0].strip()
        if not t or t.startswith(("http://", "https://")):
            continue
        cand_root = os.path.normpath(os.path.join(root, t))
        cand_file = os.path.normpath(os.path.join(dirpath, t))
        for cand in (cand_root, cand_file):
            if os.path.exists(cand) and cand.startswith(root + os.sep):
                targets.add(os.path.relpath(cand, root))
                break
    return targets


def plan_link(root, nodes, src, dst, kind, reason):
    """Validate and prepare one src -> dst link insertion.

    Returns None when src already links dst (idempotent skip), else
    (path, new_text, line). Exits non-zero on any ambiguity — the caller
    (usually a model) then falls back to a manual edit.
    """
    root = os.path.abspath(root)
    if src == dst:
        sys.exit("error: refusing to add a self-link")
    title = nodes[dst]["title"]
    if not title:
        sys.exit(f"error: link target has no frontmatter title: {dst}")
    if "[" in title or "]" in title:
        sys.exit(f"error: title of {dst} contains a square bracket — the link "
                 "label would not parse; rename the title or add the link manually")
    path = os.path.join(root, src)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if dst in _resolved_link_targets(text, root, os.path.dirname(path)):
        print(f"already linked: {src} -> {dst}")
        return None

    # Anchor: after the last existing see/ref line, else after a 関連 heading.
    matches = list(LINK_RE.finditer(text))
    if matches:
        end = text.find("\n", matches[-1].start())
        insert_at = len(text) if end == -1 else end + 1
    else:
        m = SEE_SECTION_RE.search(text)
        if not m:
            sys.exit(f"error: no see/ref line and no '## 関連' section in {src}; "
                     "add the first link manually")
        insert_at = text.find("\n", m.start()) + 1
        if text[insert_at:insert_at + 1] == "\n":
            insert_at += 1  # keep the blank line under the heading

    line = f"- {kind}: [{title}]({dst}) — {reason}\n"
    prefix = text[:insert_at]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    new_text = prefix + line + text[insert_at:]
    return path, new_text, line


def _write_atomic(path, new_text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_link_add(root, nodes, args):
    src = resolve_entry(nodes, args.src)
    dst = resolve_entry(nodes, args.dst)
    pairs = [(src, dst, args.reason)]
    if args.bidirectional:
        pairs.append((dst, src, args.reverse_reason or args.reason))

    # Validate every direction before writing anything: a bidirectional
    # request either fully applies or fully fails.
    plans = []
    for s, d, reason in pairs:
        plan = plan_link(root, nodes, s, d, args.kind, reason)
        if plan:
            plans.append((s, d) + plan)

    for s, d, path, new_text, line in plans:
        if args.dry_run:
            print(f"dry-run: would add to {s}:\n  {line}", end="")
        else:
            _write_atomic(path, new_text)
            print(f"linked: {s} -> {d}")


BANNER_MARK = "> **⚠ superseded"


def cmd_supersede(root, nodes, edges, args):
    """Mark old as replaced by new: frontmatter pair + banner + back-link.

    All three pieces are validated before anything is written, and each is
    skipped when already present, so an interrupted run can simply be re-run
    (idempotent completion). Exits non-zero on any ambiguity.
    """
    old = resolve_entry(nodes, args.old)
    new = resolve_entry(nodes, args.new)
    root_abs = os.path.abspath(root)
    if old == new:
        sys.exit("error: refusing to supersede an entry with itself")
    new_title = nodes[new]["title"]
    if not new_title:
        sys.exit(f"error: replacement entry has no frontmatter title: {new}")
    if "[" in new_title or "]" in new_title:
        sys.exit(f"error: title of {new} contains a square bracket — the banner "
                 "link label would not parse; rename the title first")

    # A superseded_by chain from the replacement must not lead back to the
    # old entry — that would create a supersede cycle.
    nxt, _prevs = supersede_maps(edges)
    cur, hops = new, 0
    while cur in nxt and hops <= len(nxt):
        cur = nxt[cur]
        hops += 1
        if cur == old:
            sys.exit(f"error: {new} is (transitively) superseded by {old} — "
                     "refusing to create a supersede cycle")

    old_path = os.path.join(root_abs, old)
    with open(old_path, encoding="utf-8") as f:
        old_text = f.read()
    meta = parse_frontmatter(old_text)
    if not meta:
        sys.exit(f"error: old entry has no frontmatter: {old}")
    existing = meta.get("superseded_by", "")
    if existing and existing != new:
        sys.exit(f"error: {old} is already superseded by {existing} — "
                 "refusing to overwrite; resolve the lineage manually")

    plans = []  # (path, new_text, done_message)

    updated = old_text
    # 1) Frontmatter: status + superseded_by as an adjacent pair.
    if meta.get("status") != "superseded" or existing != new:
        fm_end = updated.find("\n---", 3)
        head, rebuilt, inserted = updated[:fm_end].splitlines(), [], False
        for ln in head:
            if re.match(r"^superseded_by:\s*", ln):
                continue
            if re.match(r"^status:\s*", ln):
                rebuilt.append("status: superseded")
                rebuilt.append(f"superseded_by: {new}")
                inserted = True
            else:
                rebuilt.append(ln)
        if not inserted:
            rebuilt.append("status: superseded")
            rebuilt.append(f"superseded_by: {new}")
        updated = "\n".join(rebuilt) + updated[fm_end:]
    # 2) Body-top warning banner, directly under the closing delimiter.
    if BANNER_MARK not in updated:
        fm_end = updated.find("\n---", 3)
        nl = updated.find("\n", fm_end + 1)
        if nl == -1:
            updated += "\n"
            nl = len(updated) - 1
        banner = (f"\n{BANNER_MARK} ({args.date})** — current: "
                  f"[{new_title}]({new})\n")
        updated = updated[:nl + 1] + banner + updated[nl + 1:]
    if updated != old_text:
        plans.append((old_path, updated, f"marked superseded: {old} -> {new}"))

    # 3) amends back-link in the replacement (validated before any write;
    #    plan_link exits non-zero on a missing anchor or bracketed old title).
    link_plan = plan_link(root_abs, nodes, new, old, "amends", args.reason)
    if link_plan:
        path, text, line = link_plan
        plans.append((path, text, f"back-linked: {new} -> {old}\n  {line.rstrip()}"))

    if not plans:
        print(f"already superseded: {old} -> {new} (banner and back-link present)")
        return
    for path, text, message in plans:
        if args.dry_run:
            print(f"dry-run: would have {message}")
        else:
            _write_atomic(path, text)
            print(message)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=".claude/knowledge/entries",
                   help="entries root directory (default: %(default)s)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    n = sub.add_parser("neighborhood")
    n.add_argument("entry")
    n.add_argument("--depth", type=int, default=1)
    pa = sub.add_parser("path")
    pa.add_argument("a")
    pa.add_argument("b")
    lg = sub.add_parser("lineage")
    lg.add_argument("entry")
    la = sub.add_parser("link-add")
    la.add_argument("src", help="entry to edit (unique filename substring)")
    la.add_argument("dst", help="entry the new link points at")
    la.add_argument("--reason", required=True,
                    help="relationship description appended after the em dash")
    la.add_argument("--kind", choices=["see", "ref", "amends", "extends"], default="see")
    la.add_argument("--bidirectional", action="store_true",
                    help="also add the reverse link dst -> src")
    la.add_argument("--reverse-reason", default=None,
                    help="reason for the reverse link (default: --reason)")
    la.add_argument("--dry-run", action="store_true",
                    help="print planned insertions without writing")
    sp = sub.add_parser("supersede")
    sp.add_argument("old", help="entry being replaced (unique filename substring)")
    sp.add_argument("new", help="replacement entry")
    sp.add_argument("--reason", required=True,
                    help="what the replacement changes (amends back-link text)")
    sp.add_argument("--date", default=None,
                    help="banner date, YYYY-MM-DD (default: today)")
    sp.add_argument("--dry-run", action="store_true",
                    help="print planned changes without writing")
    li = sub.add_parser("lint")
    li.add_argument("files", nargs="*",
                    help="limit findings to these files (e.g. staged entries)")
    li.add_argument("--registry", default=None,
                    help="tag registry markdown (default: <root>/../CLAUDE.md)")
    args = p.parse_args()

    nodes, edges, problems = load_graph(args.root)
    if not nodes:
        sys.exit(f"error: no entries under {args.root}")

    if args.cmd == "stats":
        cmd_stats(nodes, edges, args.json)
    elif args.cmd == "neighborhood":
        cmd_neighborhood(nodes, edges, resolve_entry(nodes, args.entry), args.depth, args.json)
    elif args.cmd == "path":
        cmd_path(nodes, edges, resolve_entry(nodes, args.a), resolve_entry(nodes, args.b), args.json)
    elif args.cmd == "lineage":
        cmd_lineage(nodes, edges, resolve_entry(nodes, args.entry), args.json)
    elif args.cmd == "link-add":
        cmd_link_add(args.root, nodes, args)
    elif args.cmd == "supersede":
        if args.date is None:
            args.date = datetime.date.today().isoformat()
        cmd_supersede(args.root, nodes, edges, args)
    elif args.cmd == "lint":
        registry = args.registry or os.path.join(args.root, "..", "CLAUDE.md")
        cmd_lint(nodes, edges, problems, registry, args.files, args.json)


if __name__ == "__main__":
    main()
