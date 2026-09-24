#!/usr/bin/env python3
"""``.claude/ccmemo.json`` — the optional per-repository index configuration.

ccmemo bakes in no repository layout. The only path it knows is the
knowledge base root (``.claude/knowledge/entries``); everything else about
a repository — which directories hold documents, which files to skip, how
large a file may be before its embedding is skipped — is declared *by that
repository* in ``.claude/ccmemo.json`` (committed, shared by every clone),
or not declared at all. Without the file the index behaves as before 1.28:
``scope: kb`` (knowledge base only).

Layout of the file (every key optional)::

    {
      "index": {
        "scope": "repo",                          // "kb" (default) | "repo"
        "extensions": [".md", ".markdown", ".txt", ".rst"],
        "include": ["notes/inventory/*.yaml"],    // extra globs, any extension
        "exclude": ["drafts/**"],                 // added to the defaults
        "max_file_bytes": 262144,                 // larger: metadata only
        "corpora": [
          {"kind": "notes", "path": "notes", "conventions": "plain"}
        ]
      }
    }

Globs are matched against repository-relative POSIX paths: ``*`` and ``?``
do not cross ``/``; ``**`` does. The two default excludes are ccmemo's own
paths — session captures (``.claude/tasks/**/context-*.md``) and any
``.index/`` directory — and always apply; a repository's ``exclude`` list
adds to them.

``kind`` names the corpus a file belongs to (``kb`` for the knowledge base,
``docs`` for everything else unless a ``corpora`` rule claims the path by
longest prefix). ``conventions`` says which *vocabulary* a corpus writes
its frontmatter in: ``kb`` (the ccmemo entry schema) or ``plain`` (any
Markdown; ``status: current`` counts as ``active``). What a file actually
contains — frontmatter or not, ``related_docs``, ``- see:`` lines, inline
Markdown links — is detected per file by the extractors, never by path.

``CCMEMO_INDEX_SCOPE=kb|repo`` overrides the scope from the environment
(tests, one-off runs). Pure stdlib.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

CONFIG_RELPATH = os.path.join(".claude", "ccmemo.json")
KB_ROOT_RELPATH = ".claude/knowledge/entries"

SCOPES = ("kb", "repo")
CONVENTIONS = ("kb", "plain")
DEFAULT_EXTENSIONS = (".md", ".markdown", ".txt", ".rst")
# ccmemo's own paths: session captures are large and noisy; `.index/` is
# the derived index itself (also gitignored by the documented setup).
DEFAULT_EXCLUDES = (".claude/tasks/**/context-*.md", "**/.index/**")
DEFAULT_MAX_FILE_BYTES = 256 * 1024

# Frontmatter `status` vocabularies. The knowledge base writes `active`;
# design-document corpora commonly write `current` for the same state.
# Applied only to `plain` corpora so knowledge-base semantics never move.
STATUS_ALIASES = {"current": "active"}


def glob_to_regex(pattern: str) -> re.Pattern:
    """Compile a path glob: ``*``/``?`` stay inside one segment, ``**`` spans."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**", i):
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass
class Corpus:
    kind: str
    path: str            # repo-relative POSIX path, no trailing slash
    conventions: str     # kb | plain


@dataclass
class IndexConfig:
    scope: str = "kb"
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    corpora: list[Corpus] = field(default_factory=list)
    kb_path: str = KB_ROOT_RELPATH
    source: str = ""     # config file the values came from ('' = defaults)

    def __post_init__(self) -> None:
        self._include_re = [glob_to_regex(g) for g in self.include]
        self._exclude_re = [glob_to_regex(g) for g in self.exclude]

    # -- candidate set ------------------------------------------------------

    def excluded(self, rel: str) -> bool:
        return any(r.match(rel) for r in self._exclude_re)

    def is_candidate(self, rel: str) -> bool:
        """Repo-relative *rel* is indexed: allowed extension (or an include
        glob) and not excluded."""
        if self.excluded(rel):
            return False
        name = rel.rsplit("/", 1)[-1]
        if name == "CLAUDE.md":
            return False
        ext = os.path.splitext(name)[1].lower()
        if ext in self.extensions:
            return True
        return any(r.match(rel) for r in self._include_re)

    # -- kinds ---------------------------------------------------------------

    def kind_of(self, rel: str) -> str:
        """Kind of a repo-relative path: longest declared prefix wins; the
        knowledge base root is `kb`; anything else is `docs`."""
        best: Corpus | None = None
        for c in self.corpora:
            if rel == c.path or rel.startswith(c.path + "/"):
                if best is None or len(c.path) > len(best.path):
                    best = c
        return best.kind if best else "docs"

    def conventions_of(self, kind: str) -> str:
        for c in self.corpora:
            if c.kind == kind:
                return c.conventions
        return "kb" if kind == "kb" else "plain"

    def kinds(self) -> list[str]:
        seen = []
        for c in self.corpora:
            if c.kind not in seen:
                seen.append(c.kind)
        if "docs" not in seen:
            seen.append("docs")
        return seen


def normalize_status(status: str, conventions: str = "kb") -> str:
    """Map a corpus's status vocabulary onto the knowledge-base one."""
    s = (status or "").strip()
    if conventions == "plain":
        return STATUS_ALIASES.get(s.lower(), s)
    return s


def _warn(msg: str) -> None:
    print(f"ccmemo config: {msg}", file=sys.stderr)


def read_config_file(repo_root: str | None) -> tuple[dict, str]:
    """(parsed JSON object or {}, path read). Malformed files are reported
    on stderr and treated as absent so a typo never breaks search."""
    if not repo_root:
        return {}, ""
    path = os.path.join(repo_root, CONFIG_RELPATH)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, ""
    except (OSError, json.JSONDecodeError) as exc:
        _warn(f"{path}: {exc} — using defaults")
        return {}, ""
    if not isinstance(data, dict):
        _warn(f"{path}: top level must be an object — using defaults")
        return {}, ""
    return data, path


def index_config(repo_root: str | None, kb_path: str | None = None,
                 env: dict | None = None) -> IndexConfig:
    """Effective index configuration for the repository at *repo_root*.

    *kb_path* is the knowledge base root relative to the repository (the
    caller knows where the root it was given sits); it defaults to the
    documented location. Precedence for the scope: ``CCMEMO_INDEX_SCOPE``
    in *env* (default ``os.environ``) > the file > ``kb``.
    """
    env = os.environ if env is None else env
    raw, source = read_config_file(repo_root)
    sec = raw.get("index", {}) if isinstance(raw.get("index", {}), dict) else {}
    kb_path = (kb_path or KB_ROOT_RELPATH).strip("/").replace(os.sep, "/")

    scope = str(sec.get("scope", "kb") or "kb").strip().lower()
    if scope not in SCOPES:
        _warn(f"index.scope {scope!r} is not one of {SCOPES} — using 'kb'")
        scope = "kb"
    env_scope = str(env.get("CCMEMO_INDEX_SCOPE", "")).strip().lower()
    if env_scope in SCOPES:
        scope = env_scope
    elif env_scope:
        _warn(f"CCMEMO_INDEX_SCOPE {env_scope!r} is not one of {SCOPES} — ignored")

    exts = sec.get("extensions", list(DEFAULT_EXTENSIONS))
    if not isinstance(exts, list) or not all(isinstance(e, str) for e in exts):
        _warn("index.extensions must be a list of strings — using defaults")
        exts = list(DEFAULT_EXTENSIONS)
    extensions = tuple(e.lower() if e.startswith(".") else "." + e.lower() for e in exts)

    def _globs(key: str) -> list[str]:
        val = sec.get(key, [])
        if not isinstance(val, list) or not all(isinstance(g, str) for g in val):
            _warn(f"index.{key} must be a list of glob strings — ignored")
            return []
        return [g.strip("/") for g in val if g.strip("/")]

    include = _globs("include")
    exclude = list(DEFAULT_EXCLUDES) + [g for g in _globs("exclude") if g not in DEFAULT_EXCLUDES]

    cap = sec.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        _warn("index.max_file_bytes must be a positive integer — using the default")
        cap = DEFAULT_MAX_FILE_BYTES

    corpora = [Corpus(kind="kb", path=kb_path, conventions="kb")]
    declared = sec.get("corpora", [])
    if not isinstance(declared, list):
        _warn("index.corpora must be a list — ignored")
        declared = []
    for item in declared:
        if not isinstance(item, dict):
            _warn(f"index.corpora item {item!r} is not an object — skipped")
            continue
        kind = str(item.get("kind", "") or "").strip()
        path = str(item.get("path", "") or "").strip().strip("/").replace(os.sep, "/")
        conv = str(item.get("conventions", "") or "").strip().lower()
        if not kind or not path:
            _warn(f"index.corpora item {item!r} needs kind and path — skipped")
            continue
        if conv and conv not in CONVENTIONS:
            _warn(f"index.corpora[{kind}].conventions {conv!r} is not one of "
                  f"{CONVENTIONS} — using 'plain'")
            conv = ""
        if not conv:
            conv = "kb" if kind == "kb" else "plain"
        if kind == "kb" and path != kb_path:
            # The knowledge base root is the one path ccmemo owns: a rule
            # cannot move it, only restate it.
            _warn(f"index.corpora: kind 'kb' is always {kb_path!r} — rule for {path!r} ignored")
            continue
        corpora = [c for c in corpora if not (c.kind == kind and c.path == path)]
        corpora.append(Corpus(kind=kind, path=path, conventions=conv))

    return IndexConfig(
        scope=scope, extensions=extensions, include=include, exclude=exclude,
        max_file_bytes=cap, corpora=corpora, kb_path=kb_path, source=source,
    )
