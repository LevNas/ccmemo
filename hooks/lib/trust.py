#!/usr/bin/env python3
"""Trust family of the entry schema (schema_version 3): actors, `generated`,
`verified`, `stale_after` — plus the line-level frontmatter edits the CLI
uses to write them.

Pure stdlib, shared by ``kb_graph.py`` (plain ``python3``) and
``kb_index.py`` / ``kb_search.py`` (``uv run``), like ``frontmatter.py``.

Schema 3 fields (see docs/upgrading.md, 1.27):

* ``id`` — uuid4, the entry's identity across renames, moves and copies.
* ``generated`` — ``{by: <actor>, at: <ISO 8601>}``: who last wrote or
  meaningfully rewrote the body, and when. Required at schema 3.
* ``verified`` — list of ``{by, at}`` events, append-only: independent
  checks of the content. Optional.
* ``stale_after`` — ISO 8601 date; explicit expiry.

Actor grammar (OKF v0.2): ``human:<handle>``, ``claude-code[/<model-id>]``,
``process:<name>``.

The trust *tier* is derived from ``verified`` only: ``""`` (unverified),
``"machine"`` (latest verifier is an agent or a process) or ``"human"``. A
verification older than ``generated.at`` is expired and does not count —
rewriting the body resets verification. Lint passing is not verification.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
ACTOR_RE = re.compile(rf"^(?:human:{_NAME}|claude-code(?:/{_NAME})?|process:{_NAME})$")

TIERS = ("", "machine", "human")   # ascending


# --------------------------------------------------------------------------- #
# Actors and events
# --------------------------------------------------------------------------- #

def valid_actor(actor: Any) -> bool:
    return isinstance(actor, str) and ACTOR_RE.match(actor.strip()) is not None


def actor_kind(actor: Any) -> str:
    """'human' | 'machine' | '' (invalid)."""
    if not valid_actor(actor):
        return ""
    return "human" if actor.strip().startswith("human:") else "machine"


def human_actor(author: Any) -> str:
    """``human:<handle>`` from an ``author`` value (``"@alice"`` → ``human:alice``)."""
    handle = str(author or "").strip().strip('"').strip("'").lstrip("@").strip()
    return f"human:{handle}" if handle else ""


def parse_dt(value: Any) -> _dt.datetime | None:
    """ISO 8601 date or datetime → aware datetime (naive values are read as
    UTC so that mixed inputs stay comparable). None when unparsable."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = _dt.datetime.combine(_dt.date.fromisoformat(s), _dt.time())
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def event(value: Any) -> dict[str, str] | None:
    """``{by, at}`` with string values from a parsed mapping, else None."""
    if not isinstance(value, dict):
        return None
    by = value.get("by")
    at = value.get("at")
    if not isinstance(by, str) or not isinstance(at, str):
        return None
    return {"by": by.strip(), "at": at.strip()}


def generated_of(meta: dict) -> dict[str, str] | None:
    return event(meta.get("generated"))


def verified_events(meta: dict) -> list[dict[str, str]]:
    raw = meta.get("verified")
    if isinstance(raw, dict):      # a single mapping is tolerated
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        ev = event(item)
        if ev:
            out.append(ev)
    return out


def verified_tier(meta: dict) -> tuple[str, str]:
    """(tier, at) of the latest *valid* verification: tier in TIERS, ``at`` as
    written. Events with an invalid actor or an unparsable time are ignored;
    events older than ``generated.at`` are expired."""
    gen = generated_of(meta)
    gen_at = parse_dt(gen["at"]) if gen else None
    best: tuple[_dt.datetime, str, str] | None = None
    for ev in verified_events(meta):
        kind = actor_kind(ev["by"])
        at = parse_dt(ev["at"])
        if not kind or at is None:
            continue
        if gen_at is not None and at < gen_at:
            continue
        if best is None or at > best[0]:
            best = (at, kind, ev["at"])
    return (best[1], best[2]) if best else ("", "")


def verification_expired(meta: dict) -> bool:
    """True when the latest verification predates ``generated.at``."""
    gen = generated_of(meta)
    gen_at = parse_dt(gen["at"]) if gen else None
    if gen_at is None:
        return False
    latest = None
    for ev in verified_events(meta):
        at = parse_dt(ev["at"])
        if at is not None and (latest is None or at > latest):
            latest = at
    return latest is not None and latest < gen_at


def stale_after_passed(meta: dict, today: _dt.date | None = None) -> bool:
    d = parse_dt(meta.get("stale_after"))
    if d is None:
        return False
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    return d.date() < today


def tier_at_least(tier: str, minimum: str) -> bool:
    return TIERS.index(tier or "") >= TIERS.index(minimum or "")


def now_iso() -> str:
    """Local time with offset, seconds precision (what `verify` writes)."""
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Line-level frontmatter edits
# --------------------------------------------------------------------------- #

_FM_CLOSE_RE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)
_TOP_KEY_RE = re.compile(r"^([^\s:#\-\[{][^:]*?)\s*:")


def fm_bounds(text: str) -> tuple[int, int] | None:
    """(start, end) of the frontmatter *lines* (excluding both ``---`` lines),
    as offsets into ``text``; None when there is no well-formed frontmatter."""
    if not text.startswith("---"):
        return None
    nl = text.find("\n")
    if nl == -1 or text[:nl].rstrip("\r") != "---":
        return None
    m = _FM_CLOSE_RE.search(text, nl + 1)
    if m is None:
        return None
    return nl + 1, m.start()


def _key_span(lines: list[str], key: str) -> tuple[int, int] | None:
    """[start, end) line indexes of top-level ``key`` and its nested lines."""
    for i, ln in enumerate(lines):
        m = _TOP_KEY_RE.match(ln)
        if m and m.group(1).strip() == key:
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() == "" or nxt.startswith((" ", "\t")) or nxt.startswith("- "):
                    j += 1
                    continue
                break
            return i, j
    return None


def has_key(text: str, key: str) -> bool:
    b = fm_bounds(text)
    if b is None:
        return False
    return _key_span(text[b[0]:b[1]].splitlines(), key) is not None


def set_key(text: str, key: str, block_lines: list[str], *, after: str | None = None) -> str:
    """Replace top-level ``key`` (with its nested lines) by ``block_lines``,
    or insert the block after key ``after`` (else at the end of the
    frontmatter) when ``key`` is absent. Body and other keys are untouched."""
    b = fm_bounds(text)
    if b is None:
        raise ValueError("no frontmatter")
    head, fm, tail = text[:b[0]], text[b[0]:b[1]], text[b[1]:]
    lines = fm.splitlines()
    span = _key_span(lines, key)
    if span:
        lines[span[0]:span[1]] = block_lines
    else:
        pos = len(lines)
        if after:
            aspan = _key_span(lines, after)
            if aspan:
                pos = aspan[1]
        lines[pos:pos] = block_lines
    fm_new = "\n".join(lines)
    if fm_new:
        fm_new += "\n"
    return head + fm_new + tail


def append_list_item(text: str, key: str, item_lines: list[str], *, after: str | None = None) -> str:
    """Append one ``- ...`` item (given as its lines, already indented with
    two spaces) to the block list ``key``, creating the key when absent."""
    b = fm_bounds(text)
    if b is None:
        raise ValueError("no frontmatter")
    head, fm, tail = text[:b[0]], text[b[0]:b[1]], text[b[1]:]
    lines = fm.splitlines()
    span = _key_span(lines, key)
    if span and span[1] - span[0] > 1:
        lines[span[1]:span[1]] = item_lines
    elif span:  # `key:` present but empty (or a flow value): rewrite as a block
        lines[span[0]:span[1]] = [f"{key}:"] + item_lines
    else:
        pos = len(lines)
        if after:
            aspan = _key_span(lines, after)
            if aspan:
                pos = aspan[1]
        lines[pos:pos] = [f"{key}:"] + item_lines
    return head + "\n".join(lines) + "\n" + tail


def event_lines(by: str, at: str, *, item: bool) -> list[str]:
    """YAML lines for a ``{by, at}`` mapping — as a list item (``item=True``)
    or as the value of a top-level key (``generated``)."""
    if item:
        return [f"  - by: {by}", f"    at: {at}"]
    return [f"  by: {by}", f"  at: {at}"]
