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
  migrate --to 3         add the schema-3 fields (id, generated) where missing
  verify <entry> --by A  append a verified event (independent check of the content)
  rename <entry> <slug>  change the slug only; rewrite every link to the entry
  relink                 repair links to paths the index recorded as moved
  union-recover <file>   lossless union of two append-only copies of one file
                         that diverged across checkouts (captures, hub-entry
                         see: blocks) — verified, refuses anything else

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

union-recover automates the recovery documented in issue #24. It handles a
file left conflicted by a merge/rebase (index stages 1/2/3) or, with
--theirs <ref>, uncommitted local changes vs that ref (common ancestor =
merge-base). Both sides must be append-only against the ancestor — zero
deleted or rewritten lines — otherwise nothing is written and the exit code
is non-zero: frontmatter rewrites (updated:, status:) and body corrections
are edit-vs-edit and need a human to pick a side. The result is checked to
contain every line of every source, the previous file is backed up under
the git dir first, and --dry-run writes nothing. A see: link added on both
sides can survive twice; `lint` reports that as duplicate-link.

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
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib import frontmatter as _frontmatter  # noqa: E402
from lib.edges import LINK_RE, LOOSE_LINK_RE  # noqa: E402,F401  (shared with kb_index)
from lib import edges as _edges  # noqa: E402
from lib import trust as _trust  # noqa: E402
import tempfile
from collections import deque

# LINK_RE: strict link line (kind, text, target). LOOSE_LINK_RE: anything that
# looks like a link line; matching it but not LINK_RE means the line produces
# no edge (e.g. a square bracket inside the link text), so lint reports it as
# malformed-link instead of staying quiet. Both live in hooks/lib/edges.py.
# Both registry line forms in use: "- #tag — description (count)" and "`#tag`"
TAG_REGISTRY_RES = (
    re.compile(r"^- (#[\w\-]+)", re.MULTILINE),
    re.compile(r"^`(#[\w\-]+)`$", re.MULTILINE),
)
FILENAME_RE = re.compile(r"^\d{8}-\d{6}-.+\.md$")
# `description:` is the entry's trigger condition (when to open it). Shorter
# than the floor it cannot name a situation; longer than the ceiling it has
# become a summary. Measured on a 289-entry corpus: median 177 chars.
DESCRIPTION_MIN_CHARS = 80
DESCRIPTION_MAX_CHARS = 320

# Conventions are versioned so that upgrading the plugin never turns an
# existing corpus red overnight. A knowledge base declares the schema it
# commits to in the frontmatter of `<root>/../CLAUDE.md`
# (`schema_version: 2`); checks introduced by a later schema are still
# reported on an older corpus, but as *advisory* findings that do not affect
# the exit code, until the declaration is raised (see docs/upgrading.md).
SCHEMA_VERSION_LATEST = 3
SCHEMA_CHECKS = {
    2: {"missing-description", "description-length", "unlabeled-link",
        "amends-unreciprocated", "extends-unreciprocated"},
    3: {"missing-id", "duplicate-id", "missing-generated", "invalid-actor"},
}
# Informational at every schema: they describe the state of the knowledge,
# not a broken convention, so they never fail a pre-commit lint.
ALWAYS_ADVISORY = {"verification-expired", "stale-after-passed", "duplicate-title"}


def kb_schema_version(root, override=None):
    """Schema version the corpus under `root` declares.

    Precedence: `override` (the --schema flag) > CCMEMO_SCHEMA_VERSION env >
    `schema_version:` in the frontmatter of `<root>/../CLAUDE.md` > 1.
    """
    if override is not None:
        return int(override)
    env = os.environ.get("CCMEMO_SCHEMA_VERSION", "").strip()
    if env.isdigit():
        return int(env)
    path = os.path.join(root, "..", "CLAUDE.md")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return 1
    meta, _body = _frontmatter.parse(text)
    declared = str(meta.get("schema_version", "")).strip()
    return int(declared) if declared.isdigit() else 1


def check_severity(check, schema):
    """'error' when the check is enforced at `schema`, else 'advisory'."""
    if check in ALWAYS_ADVISORY:
        return "advisory"
    for version, checks in SCHEMA_CHECKS.items():
        if check in checks and schema < version:
            return "advisory"
    return "error"


def parse_frontmatter(text):
    """Normalized frontmatter via the shared parser (hooks/lib/frontmatter.py).

    ``tags`` is always a list of ``#tag`` strings (string or YAML-list source),
    ``status`` defaults to "active" when absent. ``title`` stays "" when the
    entry has none so lint can still report ``missing-title``.
    """
    meta, _body = _frontmatter.parse(text)
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
            sup = meta.get("superseded_by", "")
            gen = _trust.generated_of(meta)
            tier, tier_at = _trust.verified_tier(meta)
            nodes[nid] = {
                "title": meta.get("title", ""),
                "tags": set(meta.get("tags", [])),
                "status": meta.get("status", ""),
                "description": meta.get("description", ""),
                "superseded_by": sup.split("#")[0].strip(),
                "id": str(meta.get("id", "") or "").strip(),
                "generated": gen,
                "verified_tier": tier,
                "verified_at": tier_at,
            }
            # Schema 3: identity and trust family (docs/upgrading.md, 1.27).
            if not nodes[nid]["id"]:
                problems.append((nid, "missing-id", "no id: (uuid4) — run `migrate --to 3`"))
            if "generated" not in meta:
                problems.append((nid, "missing-generated",
                                 "no generated: {by, at} — run `migrate --to 3`"))
            elif gen is None or _trust.parse_dt(gen["at"]) is None:
                problems.append((nid, "missing-generated",
                                 "generated: must be a mapping with by: <actor> and at: <ISO 8601>"))
            elif not _trust.valid_actor(gen["by"]):
                problems.append((nid, "invalid-actor",
                                 f"generated.by {gen['by']!r}: expected human:<handle>, "
                                 "claude-code[/<model-id>] or process:<name>"))
            for i, ev in enumerate(_trust.verified_events(meta)):
                if not _trust.valid_actor(ev["by"]):
                    problems.append((nid, "invalid-actor",
                                     f"verified[{i}].by {ev['by']!r}: expected human:<handle>, "
                                     "claude-code[/<model-id>] or process:<name>"))
            if _trust.verification_expired(meta):
                problems.append((nid, "verification-expired",
                                 "latest verified.at is older than generated.at — the body was "
                                 "rewritten since; verify again or leave it unverified"))
            if _trust.stale_after_passed(meta):
                problems.append((nid, "stale-after-passed",
                                 f"stale_after {meta.get('stale_after')} has passed — re-check, "
                                 "then move the date or deprecate"))
            if not meta.get("title"):
                problems.append((nid, "missing-title", "no frontmatter title"))
            desc = " ".join(meta.get("description", "").split())
            if not desc:
                problems.append((nid, "missing-description",
                                 "no description: (trigger condition — when to open this entry)"))
            elif len(desc) < DESCRIPTION_MIN_CHARS:
                problems.append((nid, "description-length",
                                 f"{len(desc)} chars, under {DESCRIPTION_MIN_CHARS}: "
                                 "too short to name a situation"))
            elif len(desc) > DESCRIPTION_MAX_CHARS:
                problems.append((nid, "description-length",
                                 f"{len(desc)} chars, over {DESCRIPTION_MAX_CHARS}: "
                                 "reads as a summary, not a trigger"))
            for e in _edges.body_link_edges({}, text):
                if not e["label"]:
                    problems.append((nid, "unlabeled-link",
                                     f"{e['rel']}: ({e['target']}) has no “— why” label"))
            if not FILENAME_RE.match(fn):
                problems.append((nid, "filename", "does not match <date>-<time>-...-<slug>.md"))
            for lineno, line in enumerate(text.splitlines(), 1):
                if LOOSE_LINK_RE.match(line) and not LINK_RE.match(line):
                    problems.append((nid, "malformed-link",
                                     f"line {lineno}: does not parse as a link, "
                                     f"no edge produced: {line.strip()[:80]}"))
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
    by_id, by_title = {}, {}
    for nid, info in nodes.items():
        if info["id"]:
            by_id.setdefault(info["id"], []).append(nid)
        key = " ".join(info["title"].split()).casefold()
        if key:
            by_title.setdefault(key, []).append(nid)
    for eid, members in by_id.items():
        if len(members) > 1:
            for nid in members:
                others = ", ".join(m for m in sorted(members) if m != nid)
                problems.append((nid, "duplicate-id", f"id {eid} also in {others}"))
    for _key, members in by_title.items():
        if len(members) > 1:
            for nid in members:
                others = ", ".join(m for m in sorted(members) if m != nid)
                problems.append((nid, "duplicate-title",
                                 f"same title as {others} — hard to tell apart in search results"))
    seen = set()
    deduped = []
    for e in edges:
        key = e[:3]
        if key in seen:
            problems.append((e[0], "duplicate-link", f"{e[2]}: ({e[1]}) listed more than once"))
            continue
        seen.add(key)
        deduped.append(e)
    # A correction (amends) or elaboration (extends) that its target does not
    # point back to is invisible to anyone reading the target: require a
    # link of any kind back to the source, or a supersede chain from the
    # target that ends at the source (the replacement's amends back-link).
    pairs = {(s, d) for s, d, _k, _r in deduped}

    def supersede_chain_reaches(start, goal, limit=16):
        cur = start
        for _ in range(limit):
            cur = nodes.get(cur, {}).get("superseded_by", "")
            if not cur:
                return False
            if cur == goal:
                return True
        return False

    for src, dst, kind, _res in deduped:
        if kind not in ("amends", "extends"):
            continue
        if (dst, src) in pairs or supersede_chain_reaches(dst, src):
            continue
        problems.append((src, f"{kind}-unreciprocated",
                         f"{kind}: ({dst}) does not link back — add a see/ref line "
                         "there, or set its superseded_by to this entry"))
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


def cmd_index_md(nodes, out_path):
    """Write the whole KB as an OKF-style `index.md`: one bullet per entry,
    `* [Title](relpath) - description`, ordered by relpath (= by date).

    A by-product other tools can read without ccmemo; the description is the
    entry's trigger condition (when to open it), so the file doubles as a
    progressive-disclosure table of contents. Entries without a description
    are listed with the title only, so the gap is visible.
    """
    lines = ["# Knowledge index", ""]
    for nid in sorted(nodes):
        n = nodes[nid]
        title = " ".join((n.get("title") or nid).split())
        desc = " ".join((n.get("description") or "").split())
        flag = "" if n.get("status") in ("", "active") else f" ({n['status']})"
        lines.append(f"* [{title}]({nid}){flag}" + (f" - {desc}" if desc else ""))
    text = "\n".join(lines) + "\n"
    if out_path:
        _write_atomic(out_path, text)
        missing = sum(1 for n in nodes.values() if not n.get("description"))
        print(f"wrote {out_path}: {len(nodes)} entries, {missing} without description")
    else:
        sys.stdout.write(text)


def cmd_lint(nodes, edges, problems, registry_path, only_files, as_json, schema=1,
             root=None):
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
    enforced = [f for f in findings if check_severity(f[1], schema) == "error"]
    gated = [f for f in findings if check_severity(f[1], schema) == "advisory"
             and f[1] not in ALWAYS_ADVISORY]
    notices = [f for f in findings if f[1] in ALWAYS_ADVISORY]
    advisory = gated + notices
    if as_json:
        print(json.dumps([{"id": i, "check": c, "detail": d,
                           "severity": check_severity(c, schema)}
                          for i, c, d in findings],
                         ensure_ascii=False, indent=1))
    else:
        for nid, check, detail in enforced:
            print(f"{check:>14}  {nid}\n                {detail}")
        if gated:
            where = os.path.join(root or ".", "..", "CLAUDE.md")
            print(f"\nadvisory — schema_version {schema} declared; these checks are "
                  f"enforced at a later schema_version (latest: {SCHEMA_VERSION_LATEST})\n"
                  f"(raise `schema_version:` in the frontmatter of "
                  f"{os.path.normpath(where)} once the corpus is migrated — see docs/upgrading.md):")
            for nid, check, detail in gated:
                print(f"{check:>14}  {nid}\n                {detail}")
        if notices:
            print("\nadvisory — informational at every schema_version (never fails the lint):")
            for nid, check, detail in notices:
                print(f"{check:>14}  {nid}\n                {detail}")
        tail = f"\n{len(enforced)} finding(s)"
        if advisory:
            tail += f", {len(advisory)} advisory"
        print(tail)
    sys.exit(1 if enforced else 0)


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


class UnionRefused(Exception):
    """union-recover precondition failure: nothing may be written."""


def _git_out(cwd, *args):
    """Run git in cwd; return stdout text, or None on any failure."""
    try:
        out = subprocess.run(["git", "-C", cwd, *args],
                             capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return out.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _insertions(base, side):
    """Align base as a subsequence of side (both line lists).

    Returns {gap: [inserted lines]} where gap i sits before base[i] and
    gap len(base) is the end of file, or None when side is not append-only
    against base (some base line was deleted or rewritten).
    """
    gaps, i = {}, 0
    for line in side:
        if i < len(base) and line == base[i]:
            i += 1
        else:
            gaps.setdefault(i, []).append(line)
    return gaps if i == len(base) else None


def _is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(any(line == h for h in it) for line in needle)


def _first_lost_line(base, side):
    """1-based number and text of the first base line missing from side."""
    i = 0
    for line in side:
        if i < len(base) and line == base[i]:
            i += 1
    return i + 1, base[i]


def union_lines(base, first, second, first_name="ours", second_name="theirs"):
    """Union two append-only descendants of base; first's additions lead.

    Raises UnionRefused unless both sides are append-only. When one side's
    addition at a position wholly contains the other's (identical blocks, or
    a side that already is an earlier union), the containing block is kept
    once — this makes re-running on an already-unioned file a no-op. Any
    other overlap is kept from both.
    """
    plans = []
    for name, side in ((first_name, first), (second_name, second)):
        gaps = _insertions(base, side)
        if gaps is None:
            lineno, text = _first_lost_line(base, side)
            raise UnionRefused(
                f"{name} is not append-only: common-ancestor line {lineno} "
                f"was deleted or rewritten ({text.strip()[:60]!r}) — this is "
                "edit-vs-edit, resolve it by hand")
        plans.append(gaps)
    a, b = plans
    merged = []
    for i in range(len(base) + 1):
        ins_a, ins_b = a.get(i, []), b.get(i, [])
        if _is_subsequence(ins_a, ins_b):
            merged.extend(ins_b)
        elif _is_subsequence(ins_b, ins_a):
            merged.extend(ins_a)
        else:
            merged.extend(ins_a + ins_b)
        if i < len(base):
            merged.append(base[i])
    for name, source in (("the common ancestor", base), (first_name, first),
                         (second_name, second)):
        if not _is_subsequence(source, merged):
            raise UnionRefused(
                f"internal check failed: the union would lose lines of {name}")
    return merged


def _split_text(name, text):
    if text is None:
        raise UnionRefused(f"cannot read {name} (missing, binary, or not UTF-8)")
    if "\r" in text:
        raise UnionRefused(f"{name} has CR line endings — not supported")
    return text.splitlines()


def cmd_union_recover(args):
    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        sys.exit(f"error: no such file: {args.file}")
    top = _git_out(os.path.dirname(path), "rev-parse", "--show-toplevel")
    if top is None:
        sys.exit("error: not inside a git work tree")
    top = os.path.realpath(top.strip())
    rel = os.path.relpath(os.path.realpath(path), top).replace(os.sep, "/")

    unmerged = _git_out(top, "ls-files", "-u", "--", rel) or ""
    stages = {line.split()[2] for line in unmerged.splitlines()}
    try:
        if stages:
            if args.theirs:
                raise UnionRefused(
                    "the file is conflicted in the index — drop --theirs to "
                    "recover from stages 1/2/3")
            if stages != {"1", "2", "3"}:
                raise UnionRefused(
                    "the conflict lacks a common-ancestor or side stage "
                    "(add/add or delete) — no base to verify append-only against")
            mode = "conflict (index stages 1/2/3)"
            base, first, second = (
                _split_text(f"stage {n}", _git_out(top, "show", f":{n}:{rel}"))
                for n in ("1", "2", "3"))
            names = ("ours (stage 2)", "theirs (stage 3)")
        else:
            if not args.theirs:
                raise UnionRefused(
                    "the file is not conflicted — pass --theirs <ref> to union "
                    "local changes with that ref")
            mb = _git_out(top, "merge-base", "HEAD", args.theirs)
            if mb is None:
                raise UnionRefused(f"no merge-base between HEAD and {args.theirs}")
            mb = mb.strip()
            mode = f"local vs {args.theirs} (merge-base {mb[:12]})"
            base = _split_text("the common ancestor's copy",
                               _git_out(top, "show", f"{mb}:{rel}"))
            first = _split_text(f"the copy at {args.theirs}",
                                _git_out(top, "show", f"{args.theirs}:{rel}"))
            try:
                with open(path, encoding="utf-8") as f:
                    local = f.read()
            except (OSError, UnicodeDecodeError):
                local = None
            second = _split_text("the local file", local)
            # theirs leads, as in the documented manual procedure: the result
            # reads as "pulled first, local additions after".
            names = (f"theirs ({args.theirs})", "local")
        merged = union_lines(base, first, second, *names)
    except UnionRefused as exc:
        sys.exit(f"refused (nothing written): {exc}")

    new_text = "".join(line + "\n" for line in merged)
    summary = (f"{rel}: {mode}\n"
               f"  +{len(first) - len(base)} lines from {names[0]}, "
               f"+{len(second) - len(base)} from {names[1]} "
               f"-> {len(merged)} lines; append-only verified, no line lost")
    if args.dry_run:
        print(f"dry-run: would union {summary}")
        return

    backup_dir = _git_out(top, "rev-parse", "--git-path", "ccmemo-union-recover")
    if backup_dir is None:
        sys.exit("error: cannot locate the git dir for the backup — nothing written")
    backup_dir = os.path.join(top, backup_dir.strip())
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = os.path.join(backup_dir, f"{stamp}-{os.path.basename(path)}")
    shutil.copy2(path, backup)
    _write_atomic(path, new_text)
    print(f"unioned {summary}")
    print(f"  backup of the previous file: {backup}")
    if stages:
        print(f"  next: review, `git add {rel}`, then continue the merge/rebase")
    else:
        print("  next: review, commit, then pull --rebase; git still reports "
              f"this file as conflicted there — re-run `union-recover {rel}` "
              "(a no-op union of the same lines), `git add` it and continue")
    print("  knowledge entries: run `lint` afterwards — a link added on both "
          "sides shows up as duplicate-link")


# --------------------------------------------------------------------------- #
# Schema 3: id, generated / verified, rename / relink  (docs/upgrading.md, 1.27)
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")
_DATED_NAME_RE = re.compile(r"^(\d{8}-\d{6}-[^-]+)-(.+)\.md$")
_MD_TARGET_RE = re.compile(r"(\]\()([^)\s]+)(\))")
_SUPERSEDED_BY_LINE_RE = re.compile(r"^(superseded_by:\s*)(\S+)(.*)$", re.MULTILINE)


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _corpus_tz(spec):
    """tzinfo from ``+09:00`` / ``-05:30`` / ``Z``; default: this machine's
    current offset (the corpus is assumed to have been written here)."""
    if not spec:
        return datetime.datetime.now().astimezone().tzinfo
    s = spec.strip()
    if s.upper() == "Z":
        return datetime.timezone.utc
    m = re.match(r"^([+-])(\d{2}):?(\d{2})$", s)
    if not m:
        sys.exit(f"error: --tz must look like +09:00 (got {spec!r})")
    sign = 1 if m.group(1) == "+" else -1
    delta = datetime.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return datetime.timezone(sign * delta)


def _filename_timestamp(nid, tz):
    """``generated.at`` for migrate: the filename's date-time in the corpus TZ."""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-", os.path.basename(nid))
    if not m:
        return None
    try:
        return datetime.datetime(*map(int, m.groups()), tzinfo=tz).isoformat()
    except ValueError:
        return None


def cmd_migrate(root, nodes, args):
    """Bring every entry up to schema `--to` without touching anything else.

    Schema 3 (idempotent): insert ``id: <uuid4>`` where missing and
    ``generated: {by, at}`` where missing — ``at`` is the filename's
    date-time in the corpus time zone (``--tz``), else ``created``, else
    now. ``verified`` is never backfilled: nobody has independently checked
    the existing entries, so they honestly start unverified. ``confidence``
    is left in place (retired, still parsed, never used).
    """
    if args.to != 3:
        sys.exit(f"error: migrate knows schema 3 only (got --to {args.to})")
    if not _trust.valid_actor(args.by):
        sys.exit(f"error: --by must be human:<handle>, claude-code[/<model>] or "
                 f"process:<name> (got {args.by!r})")
    root_abs = os.path.abspath(root)
    tz = _corpus_tz(args.tz)
    n_id = n_gen = n_same = n_skip = n_changed = 0
    for nid in sorted(nodes):
        path = os.path.join(root_abs, nid)
        text = _read_text(path)
        raw, _body = _frontmatter.parse_raw(text)
        if not _frontmatter.has_frontmatter(text):
            print(f"skip (no frontmatter): {nid}")
            n_skip += 1
            continue
        new_text = text
        added = []
        if not str(raw.get("id", "")).strip():
            new_text = _trust.set_key(new_text, "id", [f"id: {uuid.uuid4()}"], after="title")
            added.append("id")
            n_id += 1
        if "generated" not in raw:
            at = _filename_timestamp(nid, tz)
            if at is None:
                created = str(raw.get("created", "")).strip()
                d = _trust.parse_dt(created)
                at = (d.replace(tzinfo=tz).isoformat() if d is not None
                      else datetime.datetime.now(tz).replace(microsecond=0).isoformat())
            anchor = "created" if "created" in raw else "id"
            new_text = _trust.set_key(
                new_text, "generated", ["generated:"] + _trust.event_lines(args.by, at, item=False),
                after=anchor)
            added.append("generated")
            n_gen += 1
        if new_text == text:
            n_same += 1
            continue
        n_changed += 1
        if args.dry_run:
            print(f"dry-run: would add {', '.join(added)} to {nid}")
        else:
            _write_atomic(path, new_text)
            print(f"migrated ({', '.join(added)}): {nid}")
    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {n_changed} entr(y/ies): "
          f"id added to {n_id}, generated added to {n_gen}; {n_same} already at schema 3"
          + (f", {n_skip} skipped" if n_skip else ""))
    if not args.dry_run and (n_id or n_gen):
        print("next: declare `schema_version: 3` in the frontmatter of "
              f"{os.path.normpath(os.path.join(root, '..', 'CLAUDE.md'))}, then run `lint`")


def cmd_verify(root, nodes, args):
    """Append one ``verified`` event — the deterministic way to record that
    someone (a person, an agent session, a gate process) independently
    checked the entry's content. Lint passing is *not* verification."""
    nid = resolve_entry(nodes, args.entry)
    by = args.by.strip()
    if not _trust.valid_actor(by):
        sys.exit(f"error: --by must be human:<handle>, claude-code[/<model>] or "
                 f"process:<name> (got {args.by!r})")
    at = (args.at or _trust.now_iso()).strip()
    if _trust.parse_dt(at) is None:
        sys.exit(f"error: --at must be ISO 8601 (got {args.at!r})")
    path = os.path.join(os.path.abspath(root), nid)
    text = _read_text(path)
    if not _frontmatter.has_frontmatter(text):
        sys.exit(f"error: no frontmatter: {nid}")
    meta = parse_frontmatter(text)
    for ev in _trust.verified_events(meta):
        if ev["by"] == by and ev["at"] == at:
            print(f"already recorded: {nid} verified by {by} at {at}")
            return
    gen = _trust.generated_of(meta)
    if gen is None:
        print(f"note: {nid} has no generated: — run `migrate --to 3` so expiry can be derived",
              file=sys.stderr)
    elif (_trust.parse_dt(at) or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)) \
            < (_trust.parse_dt(gen["at"]) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)):
        print(f"note: {at} is older than generated.at {gen['at']} — this event counts as expired",
              file=sys.stderr)
    new_text = _trust.append_list_item(
        text, "verified", _trust.event_lines(by, at, item=True), after="generated")
    if args.dry_run:
        print(f"dry-run: would record in {nid}:\n  - by: {by}\n    at: {at}")
        return
    _write_atomic(path, new_text)
    tier, _ = _trust.verified_tier(parse_frontmatter(new_text))
    print(f"verified: {nid} by {by} at {at} (tier: {tier or 'unverified'})")


def _rewrite_targets(text, dirpath, root_abs, old_abs, new_abs):
    """Point every Markdown link target and ``superseded_by:`` value that
    resolves to ``old_abs`` at ``new_abs``, keeping each link's style
    (entries-root-relative stays root-relative, directory-relative stays
    directory-relative). Returns the new text (unchanged when nothing
    pointed at old_abs)."""
    def fix_target(t):
        core, sep, frag = t.partition("#")
        if not core or core.startswith(("http://", "https://")):
            return t
        if os.path.normpath(os.path.join(root_abs, core)) == old_abs:
            return os.path.relpath(new_abs, root_abs) + sep + frag
        if os.path.normpath(os.path.join(dirpath, core)) == old_abs:
            return os.path.relpath(new_abs, dirpath) + sep + frag
        return t

    out = _MD_TARGET_RE.sub(lambda m: m.group(1) + fix_target(m.group(2)) + m.group(3), text)
    b = _trust.fm_bounds(out)
    if b:
        fm = out[b[0]:b[1]]
        fm2 = _SUPERSEDED_BY_LINE_RE.sub(
            lambda m: m.group(1) + fix_target(m.group(2)) + m.group(3), fm)
        out = out[:b[0]] + fm2 + out[b[1]:]
    return out


def _relink_plans(root_abs, nodes, old_rel, new_rel, skip=()):
    old_abs = os.path.normpath(os.path.join(root_abs, old_rel))
    new_abs = os.path.normpath(os.path.join(root_abs, new_rel))
    plans = []
    for nid in sorted(nodes):
        if nid in skip:
            continue
        path = os.path.join(root_abs, nid)
        text = _read_text(path)
        new_text = _rewrite_targets(text, os.path.dirname(path), root_abs, old_abs, new_abs)
        if new_text != text:
            plans.append((nid, path, new_text))
    return plans


def cmd_rename(root, nodes, args):
    """Change an entry's slug only. The date-time prefix (creation time — a
    fact), the author segment and the ``YYYY/MM/`` directory stay; every
    link and ``superseded_by:`` in the corpus is rewritten to the new path.
    ``id`` keeps the entry's identity across the rename."""
    nid = resolve_entry(nodes, args.entry)
    root_abs = os.path.abspath(root)
    base = os.path.basename(nid)
    m = _DATED_NAME_RE.match(base)
    if not m:
        sys.exit(f"error: {nid} is not named <date>-<time>-<author>-<slug>.md; rename by hand")
    prefix = m.group(1)
    # The author segment may itself contain hyphens (`lev-nas`): when the
    # frontmatter names the author, trust it over the first-hyphen split.
    author = _trust.human_actor(parse_frontmatter(_read_text(os.path.join(root_abs, nid))).get("author"))
    handle = author[len("human:"):] if author else ""
    if handle and base.startswith(f"{base[:15]}-{handle}-"):
        prefix = f"{base[:15]}-{handle}"
    slug = args.new_slug.strip()
    if not _SLUG_RE.match(slug):
        sys.exit(f"error: slug must be kebab-case [a-z0-9-] (got {slug!r})")
    new_base = f"{prefix}-{slug}.md"
    if new_base == base:
        print(f"already named: {nid}")
        return
    new_rel = os.path.join(os.path.dirname(nid), new_base) if os.path.dirname(nid) else new_base
    new_abs = os.path.join(root_abs, new_rel)
    if os.path.exists(new_abs):
        sys.exit(f"error: {new_rel} already exists")
    plans = _relink_plans(root_abs, nodes, nid, new_rel, skip=(nid,))
    if args.dry_run:
        print(f"dry-run: would rename {nid} -> {new_rel}")
        for src, _p, _t in plans:
            print(f"dry-run: would rewrite links in {src}")
        return
    for _src, path, new_text in plans:
        _write_atomic(path, new_text)
    os.rename(os.path.join(root_abs, nid), new_abs)
    print(f"renamed: {nid} -> {new_rel}")
    for src, _p, _t in plans:
        print(f"relinked: {src}")
    if not plans:
        print("(no entry linked to it)")


def _index_db_path(root):
    """`<root>/../.index/kb.db` — kb_index.index_db_path without importing it
    (kb_graph stays plain-python3; the vector tables are never touched)."""
    return os.path.normpath(os.path.join(os.path.abspath(root), "..", ".index", "kb.db"))


def cmd_relink(root, nodes, args):
    """Repair links to paths the index knows have moved.

    ``kb_index.py`` records a move whenever a re-index finds a known ``id``
    at a new relpath while the old file is gone (a manual ``mv`` or a rename
    done outside ``rename``). This command rewrites every link and
    ``superseded_by:`` that still points at an old path, in the order the
    moves were recorded, so chains resolve hop by hop.
    """
    db = _index_db_path(root)
    if not os.path.exists(db):
        sys.exit(f"error: no index at {db} — run a search or `kb_index.py` first")
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "moves" not in names:
            print("nothing to relink (index predates move tracking; a re-index adds it)")
            return
        moves = conn.execute(
            "SELECT id, old_relpath, new_relpath FROM moves ORDER BY rowid").fetchall()
    finally:
        conn.close()
    root_abs = os.path.abspath(root)
    total = 0
    for eid, old_rel, new_rel in moves:
        if new_rel not in nodes or old_rel in nodes:
            continue  # target gone again, or the old path is back: not a live move
        plans = _relink_plans(root_abs, nodes, old_rel, new_rel)
        for src, path, new_text in plans:
            if args.dry_run:
                print(f"dry-run: would relink {src}: {old_rel} -> {new_rel}")
            else:
                _write_atomic(path, new_text)
                print(f"relinked {src}: {old_rel} -> {new_rel}")
            total += 1
    if total == 0:
        print("nothing to relink")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=".claude/knowledge/entries",
                   help="entries root directory (default: %(default)s)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--schema", type=int, default=None, metavar="N",
                   help="lint as if the corpus declared schema_version N "
                        "(default: the declaration in <root>/../CLAUDE.md, else 1)")
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
    im = sub.add_parser("index-md",
                        help="write the KB as an OKF-style index.md (title + description per entry)")
    im.add_argument("--out", default=None, metavar="FILE",
                    help="output file (default: stdout)")
    mg = sub.add_parser("migrate", help="add the schema-3 fields where missing (idempotent)")
    mg.add_argument("--to", type=int, required=True, metavar="N",
                    help="target schema_version (3)")
    mg.add_argument("--by", default="claude-code",
                    help="generated.by actor for entries without one (default: %(default)s)")
    mg.add_argument("--tz", default=None, metavar="+HH:MM",
                    help="time zone of the filename timestamps (default: this machine's)")
    mg.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    vf = sub.add_parser("verify", help="append a verified event to an entry")
    vf.add_argument("entry", help="entry to mark (unique filename substring)")
    vf.add_argument("--by", required=True,
                    help="who checked it: human:<handle> | claude-code[/<model-id>] | process:<name>")
    vf.add_argument("--at", default=None, help="ISO 8601 datetime (default: now)")
    vf.add_argument("--dry-run", action="store_true",
                    help="print the event without writing")
    rn = sub.add_parser("rename", help="change an entry's slug and rewrite every link to it")
    rn.add_argument("entry", help="entry to rename (unique filename substring)")
    rn.add_argument("new_slug", help="new kebab-case slug (prefix and directory stay)")
    rn.add_argument("--dry-run", action="store_true",
                    help="print the planned rename and rewrites without writing")
    rl = sub.add_parser("relink", help="repair links to paths the index recorded as moved")
    rl.add_argument("--dry-run", action="store_true",
                    help="print the planned rewrites without writing")
    li = sub.add_parser("lint")
    li.add_argument("files", nargs="*",
                    help="limit findings to these files (e.g. staged entries)")
    li.add_argument("--registry", default=None,
                    help="tag registry markdown (default: <root>/../CLAUDE.md)")
    ur = sub.add_parser(
        "union-recover",
        help="lossless union of two append-only copies of one file (issue #24)",
        description=(
            "Union a file that diverged across checkouts. Without --theirs the "
            "file must be conflicted by a merge/rebase (index stages 1/2/3 are "
            "used); with --theirs <ref> the uncommitted local copy is unioned "
            "with that ref (common ancestor = merge-base). Both sides must be "
            "append-only against the ancestor, otherwise nothing is written "
            "and the exit code is non-zero — frontmatter rewrites (updated:, "
            "status:) and body corrections are edit-vs-edit: pick a side by "
            "hand. The previous file is backed up under the git dir. A see: "
            "link added on both sides may remain twice: run `lint` afterwards, "
            "it reports duplicate-link."))
    ur.add_argument("file", help="path of the diverged file")
    ur.add_argument("--theirs", default=None, metavar="REF",
                    help="union uncommitted local changes with this ref "
                         "(e.g. origin/main after `git fetch`)")
    ur.add_argument("--dry-run", action="store_true",
                    help="verify and report without writing")
    args = p.parse_args()

    if args.cmd == "union-recover":
        # file-level git recovery: needs no entry graph (or any KB at all)
        cmd_union_recover(args)
        return

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
    elif args.cmd == "index-md":
        cmd_index_md(nodes, args.out)
    elif args.cmd == "migrate":
        cmd_migrate(args.root, nodes, args)
    elif args.cmd == "verify":
        cmd_verify(args.root, nodes, args)
    elif args.cmd == "rename":
        cmd_rename(args.root, nodes, args)
    elif args.cmd == "relink":
        cmd_relink(args.root, nodes, args)
    elif args.cmd == "lint":
        registry = args.registry or os.path.join(args.root, "..", "CLAUDE.md")
        cmd_lint(nodes, edges, problems, registry, args.files, args.json,
                 schema=kb_schema_version(args.root, args.schema), root=args.root)


if __name__ == "__main__":
    main()
