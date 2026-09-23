#!/usr/bin/env python3
"""Typed link edges shared by the index, the graph CLI and the search.

A knowledge entry links to its neighbours from the body, one bullet per link::

    - see: [Title of the target](2026/09/20260923-143000-user-slug.md) — why to follow it
    - amends: [Older entry](2026/08/...md) — what this entry corrects

The part after the closing parenthesis is the *label*: the one-line reason the
link exists. On a real corpus nine links in ten carry one, and it is what lets a
reader decide whether to open the target without reading it. Before this module
every consumer kept only the target path (``kb_index.py``) or only the kind
(``kb_graph.py``); the label was never stored anywhere.

``extract_edges(frontmatter, body)`` returns ``[{"target", "rel", "label"}]``
in document order. Two extractors are registered and both run on every file,
so a corpus can mix conventions without configuration:

* ``body-links`` — the ``- see:/ref:/amends:/extends:`` bullets above (the
  ccmemo knowledge-base convention).
* ``related-docs`` — a frontmatter ``related_docs:`` list (design-document
  convention): items are ``{path, label}`` mappings (optional ``rel``/``kind``,
  default ``see``) or bare path strings.

Targets are returned as written (anchor ``#...`` removed, ``http(s)://``
links skipped); resolving them against the entries root is the caller's job,
because only the caller knows the root and the linking file's directory.
Pure stdlib — importable from the hooks, the graph CLI and ``uv run`` scripts.
"""
from __future__ import annotations

import re
from typing import Any, Callable

EDGE_KINDS = ("see", "ref", "amends", "extends")

# Strict shape of a link line: the three groups (kind, link text, target) are
# what kb_graph.py has always consumed via ``findall``; keep the group layout.
LINK_RE = re.compile(
    r"^\s*-\s+(see|ref|amends|extends):\s*\[([^\]]*)\]\(([^)]+)\)", re.MULTILINE
)
# Loose shape: matches LOOSE but not LINK_RE means a line that *looks* like a
# link yet produces no edge (e.g. a square bracket inside the link text).
LOOSE_LINK_RE = re.compile(r"^\s*-\s+(see|ref|amends|extends):\s*\[")
# Same as LINK_RE plus the trailing label text on the line.
_EDGE_LINE_RE = re.compile(
    r"^\s*-\s+(see|ref|amends|extends):\s*\[([^\]]*)\]\(([^)]+)\)[ \t]*(.*?)[ \t]*$",
    re.MULTILINE,
)
# Separators writers put between the link and the label: em/en dash, hyphen,
# ASCII or full-width colon. Stripped so the stored label starts with content.
_LABEL_SEP_RE = re.compile(r"^[\s—–\-:：]+")


def clean_target(raw: str) -> str:
    """Drop a ``#anchor`` and surrounding whitespace; '' for external links."""
    t = raw.split("#")[0].strip()
    if not t or t.startswith(("http://", "https://")):
        return ""
    return t


def clean_label(raw: str) -> str:
    """Strip the leading separator and collapse whitespace."""
    return " ".join(_LABEL_SEP_RE.sub("", raw or "").split())


def body_link_edges(frontmatter: dict[str, Any], body: str) -> list[dict[str, str]]:
    """Edges from ``- see:``-style bullets in the body, in document order."""
    edges: list[dict[str, str]] = []
    for kind, _text, target, label in _EDGE_LINE_RE.findall(body):
        t = clean_target(target)
        if not t:
            continue
        edges.append({"target": t, "rel": kind, "label": clean_label(label)})
    return edges


def related_docs_edges(frontmatter: dict[str, Any], body: str) -> list[dict[str, str]]:
    """Edges from a frontmatter ``related_docs:`` list (design-document corpora)."""
    items = frontmatter.get("related_docs")
    if not isinstance(items, list):
        return []
    edges: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            path = str(item.get("path") or item.get("target") or "")
            rel = str(item.get("rel") or item.get("kind") or "see")
            label = str(item.get("label") or item.get("reason") or "")
        else:
            path, rel, label = str(item), "see", ""
        t = clean_target(path)
        if not t:
            continue
        if rel not in EDGE_KINDS:
            rel = "see"
        edges.append({"target": t, "rel": rel, "label": clean_label(label)})
    return edges


Extractor = Callable[[dict[str, Any], str], list[dict[str, str]]]

# Registry: name -> extractor. Every registered extractor runs on every file;
# a corpus that uses only one convention simply yields nothing from the other.
EXTRACTORS: dict[str, Extractor] = {
    "body-links": body_link_edges,
    "related-docs": related_docs_edges,
}


def extract_edges(frontmatter: dict[str, Any], body: str,
                  extractors: list[str] | None = None) -> list[dict[str, str]]:
    """Run the registered extractors (all of them by default) and merge.

    Duplicate (target, rel) pairs keep the first occurrence so a link written
    both in the body and in ``related_docs`` counts once.
    """
    names = list(EXTRACTORS) if extractors is None else extractors
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name in names:
        fn = EXTRACTORS.get(name)
        if fn is None:
            raise KeyError(f"unknown edge extractor: {name}")
        for e in fn(frontmatter, body):
            key = (e["target"], e["rel"])
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
    return out
