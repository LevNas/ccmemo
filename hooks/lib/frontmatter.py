#!/usr/bin/env python3
"""Single YAML-frontmatter parser shared by every ccmemo reader.

Pure stdlib — runs under plain ``python3`` (hooks, ``kb_graph.py``) and under
``uv run`` (``kb_index.py`` / ``kb_search.py``) alike. Before this module each
reader carried its own flat ``key: value`` splitter (four of them), none of
which could read a YAML block list, so ``tags:`` written as a list was silently
parsed as *no tags* and ``status:`` defaults disagreed between the search
scripts (missing → "") and the prompt hook (missing → "active").

Supported YAML subset (everything the entry schema uses or is planned to use):

* ``key: scalar`` — bare, ``"double"`` or ``'single'`` quoted. A trailing
  ``# comment`` is stripped from bare scalars only when the ``#`` is followed
  by whitespace, so an unquoted ``tags: #a #b`` keeps working.
* block sequences — ``key:`` followed by ``- item`` lines (same or deeper
  indent); items may be scalars or one-level mappings (``- by: x`` + ``  at: y``)
* block mappings nested by indentation (``generated:`` + ``  by: x``)
* flow sequences ``[a, b]`` and flow mappings ``{k: v, k2: v2}`` (no nesting
  inside flow collections)

Everything else (anchors, multi-line ``|``/``>`` scalars, explicit tags) is out
of scope; such values come back as the raw string so nothing crashes. Values
are never type-coerced — every scalar is a ``str`` (``created: 2026-09-22``
stays a string that sorts lexically).

``parse()`` returns the *normalized* view every reader should consume:
``title``/``status``/``type``/``created``/``superseded_by``/``description``
are always present as strings, ``status`` defaults to ``"active"`` when absent
or blank (the documented convention), and ``tags`` is always a list of
``#tag`` strings whether the source wrote a quoted string or a YAML list.
Unknown keys are preserved untouched.

CLI (used by the bash prompt hook to avoid one interpreter start per file)::

    python3 frontmatter.py [--sep SEP] --fields status,superseded_by,title FILE...

prints one line per file: ``path`` followed by the requested fields, joined
by ``SEP`` (default tab; separator/newline characters inside values are
collapsed to spaces, ``tags`` joined by spaces). Pass a non-whitespace
separator such as ``$'\x1f'`` when reading with bash ``read`` — bash
collapses runs of whitespace IFS characters, so an empty middle field would
otherwise shift the columns. Unreadable files yield empty fields. Always
exits 0.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

TAG_RE = re.compile(r"#[A-Za-z0-9][A-Za-z0-9_-]*")
DEFAULT_STATUS = "active"
# Fields every reader may rely on being present (as str) after normalize().
SCALAR_FIELDS = ("title", "status", "type", "created", "superseded_by",
                 "description", "author", "confidence")

_KEY_RE = re.compile(r"^([^\s:#\-\[{][^:]*?)\s*:(?:\s+(.*))?$")
_DELIM_RE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------- #

def split(text: str) -> tuple[str | None, str]:
    """Return (frontmatter block without delimiters, body).

    ``block`` is ``None`` when the text has no well-formed frontmatter (no
    leading ``---`` line, or no closing ``---`` line). The body keeps its
    original text minus the leading blank lines after the closing delimiter.
    """
    if not text.startswith("---"):
        return None, text
    first_nl = text.find("\n")
    if first_nl == -1 or text[:first_nl].rstrip("\r") != "---":
        return None, text
    rest = text[first_nl + 1:]
    m = _DELIM_RE.search(rest)
    if m is None:
        return None, text
    block = rest[:m.start()]
    body = rest[m.end():].lstrip("\r\n")
    return block, body


# --------------------------------------------------------------------------- #
# YAML subset
# --------------------------------------------------------------------------- #

def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _strip_comment(s: str) -> str:
    """Drop a trailing ``# comment`` from a bare scalar.

    Only a ``#`` that is preceded by whitespace *and* followed by whitespace
    (or end of line) counts, so ``#tag`` tokens survive.
    """
    out = re.split(r"\s+#(?:\s|$)", s, maxsplit=1)[0]
    return out.rstrip()


def _split_flow(inner: str) -> list[str]:
    """Split a flow collection body on commas outside quotes."""
    items, buf, quote = [], [], None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail or items:
        items.append(tail)
    return [i for i in items if i != ""]


def _scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if s[0] in "\"'":
        return _unquote(s)
    if s.startswith("[") and s.endswith("]"):
        return [_scalar(x) for x in _split_flow(s[1:-1])]
    if s.startswith("{") and s.endswith("}"):
        out: dict[str, Any] = {}
        for pair in _split_flow(s[1:-1]):
            m = _KEY_RE.match(pair)
            if m:
                out[m.group(1)] = _scalar(m.group(2) or "")
        return out
    return _strip_comment(s)


def _lines(block: str) -> list[tuple[int, str]]:
    out = []
    for raw in block.splitlines():
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        out.append((indent, stripped))
    return out


def _is_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _parse_mapping(lines, i, indent):
    out: dict[str, Any] = {}
    n = len(lines)
    while i < n:
        ind, content = lines[i]
        if ind != indent or _is_item(content):
            break
        m = _KEY_RE.match(content)
        if not m:
            i += 1  # not a key line at this level: skip rather than fail
            continue
        key, rest = m.group(1), m.group(2)
        i += 1
        if rest is not None and rest.strip() != "":
            out[key] = _scalar(rest)
            continue
        # Empty value: look at the next line for a nested block.
        if i < n:
            nind, ncontent = lines[i]
            if _is_item(ncontent) and nind >= indent:
                out[key], i = _parse_sequence(lines, i, nind)
                continue
            if nind > indent:
                out[key], i = _parse_mapping(lines, i, nind)
                continue
        out[key] = ""
    return out, i


def _parse_sequence(lines, i, indent):
    out: list[Any] = []
    n = len(lines)
    while i < n:
        ind, content = lines[i]
        if ind != indent or not _is_item(content):
            break
        item = content[1:].strip()
        i += 1
        if item == "":
            if i < n and lines[i][0] > indent:
                nind, ncontent = lines[i]
                if _is_item(ncontent):
                    val, i = _parse_sequence(lines, i, nind)
                else:
                    val, i = _parse_mapping(lines, i, nind)
                out.append(val)
            else:
                out.append("")
            continue
        km = _KEY_RE.match(item)
        if km and not item[0] in "\"'[{":
            # "- key: value" opens a mapping; continuation keys sit at the
            # column where "key" started (indent + 2 for "- ").
            child_indent = indent + 2
            first: dict[str, Any] = {}
            rest = km.group(2)
            if rest is not None and rest.strip() != "":
                first[km.group(1)] = _scalar(rest)
            elif i < n and lines[i][0] > child_indent:
                nested, i = (_parse_sequence if _is_item(lines[i][1]) else _parse_mapping)(
                    lines, i, lines[i][0])
                first[km.group(1)] = nested
            else:
                first[km.group(1)] = ""
            more, i = _parse_mapping(lines, i, child_indent)
            first.update(more)
            out.append(first)
            continue
        out.append(_scalar(item))
    return out, i


def parse_block(block: str) -> dict[str, Any]:
    """Parse a frontmatter block (delimiters already removed) into a dict."""
    lines = _lines(block)
    if not lines:
        return {}
    meta, _ = _parse_mapping(lines, 0, lines[0][0])
    return meta


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

def normalize_tags(value: Any) -> list[str]:
    """Return ``#tag`` strings from either form.

    * string  — ``"#a #b"`` (the historical single-line form; commas tolerated)
    * list    — ``["#a", "b"]`` (YAML block or flow list; a missing ``#`` is added)
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        tokens = _unquote(value.strip()).replace(",", " ").split()
    elif isinstance(value, list):
        tokens = [t for t in value if isinstance(t, str)]
    else:
        return []
    seen: list[str] = []
    for tok in tokens:
        tok = _unquote(tok.strip())
        if not tok:
            continue
        if not tok.startswith("#"):
            tok = "#" + tok
        if TAG_RE.fullmatch(tok) and tok not in seen:
            seen.append(tok)
    return seen


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def normalize(meta: dict[str, Any]) -> dict[str, Any]:
    """Canonical view: scalar fields always str, status defaulted, tags a list."""
    out: dict[str, Any] = dict(meta)
    for key in SCALAR_FIELDS:
        out[key] = _as_str(meta.get(key))
    if not out["status"]:
        out["status"] = DEFAULT_STATUS
    out["tags"] = normalize_tags(meta.get("tags"))
    return out


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Return (normalized frontmatter, body) for a whole entry text."""
    block, body = split(text)
    meta = parse_block(block) if block is not None else {}
    return normalize(meta), body


def parse_raw(text: str) -> tuple[dict[str, Any], str]:
    """Like :func:`parse` but without normalization (missing keys stay missing)."""
    block, body = split(text)
    return (parse_block(block) if block is not None else {}), body


def has_frontmatter(text: str) -> bool:
    return split(text)[0] is not None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _flat(value: Any, sep: str) -> str:
    if isinstance(value, list):
        value = " ".join(_flat(v, sep) for v in value)
    elif isinstance(value, dict):
        value = " ".join(f"{k}={_flat(v, sep)}" for k, v in value.items())
    return str(value).replace(sep, " ").replace("\n", " ")


def main(argv: list[str]) -> int:
    fields = ["status", "superseded_by", "title"]
    sep = "\t"
    files: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--fields":
            fields = [f for f in next(it, "").split(",") if f]
        elif arg == "--sep":
            sep = next(it, "\t") or "\t"
        elif arg == "--":
            files.extend(it)
        else:
            files.append(arg)
    out = sys.stdout
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                meta, _ = parse(fh.read())
        except OSError:
            meta = normalize({})
        cols = [path] + [_flat(meta.get(f, ""), sep) for f in fields]
        out.write(sep.join(cols) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
