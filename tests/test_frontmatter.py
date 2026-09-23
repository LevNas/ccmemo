#!/usr/bin/env python3
"""Self-tests for hooks/lib/frontmatter.py — the single frontmatter parser.

Run: python3 tests/test_frontmatter.py   (exit 0 = all pass)

Pure stdlib. When PyYAML happens to be importable the block parser is also
cross-checked against ``yaml.safe_load`` on every fixture (skipped otherwise,
so the suite never needs a dependency the plugin itself does not have).
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib import frontmatter as fm  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "..", "hooks", "lib", "frontmatter.py")

STRING_TAGS = (
    "---\n"
    "title: ADR: colon inside — unquoted\n"
    'author: "@someone"\n'
    "created: 2026-09-22\n"
    "status: active\n"
    "type: knowledge\n"
    "confidence: low\n"
    'tags: "#a #b-c #1password"\n'
    "---\n"
    "\n"
    "body line\n"
)

LIST_TAGS = (
    "---\n"
    'title: "Quoted title"\n'
    "tags:\n"
    '  - "#a"\n'
    "  - b\n"
    "  - '#c'\n"
    "status:\n"
    "---\n"
    "body\n"
)

NESTED = (
    "---\n"
    "title: t\n"
    "tags: [x, \"#y\", 'z']\n"
    "generated:\n"
    "  by: claude-code/some-model\n"
    "  at: 2026-09-23T10:00:00Z\n"
    "verified:\n"
    "  - by: human:someone\n"
    "    at: 2026-09-24\n"
    "  - {by: process:nightly, at: 2026-09-25}\n"
    "superseded_by: 2026/09/x.md   # trailing comment\n"
    "# a full-line comment\n"
    "---\n"
)


def test_split_and_body():
    block, body = fm.split(STRING_TAGS)
    assert block is not None and block.startswith("title:"), block
    assert body == "body line\n", repr(body)
    assert fm.split("no frontmatter\n---\nx")[0] is None
    assert fm.split("---\ntitle: unclosed\nbody")[0] is None
    assert fm.split("---\r\ntitle: crlf\r\n---\r\nbody")[0] == "title: crlf\r\n"


def test_string_tags_and_unquoted_colon_title():
    meta, _ = fm.parse(STRING_TAGS)
    assert meta["title"] == "ADR: colon inside — unquoted", meta["title"]
    assert meta["author"] == "@someone"
    assert meta["tags"] == ["#a", "#b-c", "#1password"], meta["tags"]
    assert meta["status"] == "active" and meta["type"] == "knowledge"
    assert meta["created"] == "2026-09-22"


def test_list_tags_and_blank_status_defaults_to_active():
    meta, body = fm.parse(LIST_TAGS)
    assert meta["title"] == "Quoted title"
    assert meta["tags"] == ["#a", "#b", "#c"], meta["tags"]
    assert meta["status"] == "active", meta["status"]
    assert body == "body\n"


def test_same_indent_sequence():
    meta, _ = fm.parse("---\ntags:\n- \"#q\"\n- r\n---\n")
    assert meta["tags"] == ["#q", "#r"], meta["tags"]


def test_flow_and_nested_collections():
    meta, _ = fm.parse(NESTED)
    assert meta["tags"] == ["#x", "#y", "#z"], meta["tags"]
    assert meta["generated"] == {"by": "claude-code/some-model", "at": "2026-09-23T10:00:00Z"}, meta["generated"]
    assert meta["verified"] == [
        {"by": "human:someone", "at": "2026-09-24"},
        {"by": "process:nightly", "at": "2026-09-25"},
    ], meta["verified"]
    assert meta["superseded_by"] == "2026/09/x.md", meta["superseded_by"]


def test_unquoted_hash_tags_survive_comment_stripping():
    meta, _ = fm.parse("---\ntags: #a #b\n---\n")
    assert meta["tags"] == ["#a", "#b"], meta["tags"]
    meta, _ = fm.parse("---\ntitle: x  # real comment\n---\n")
    assert meta["title"] == "x", meta["title"]


def test_missing_frontmatter_is_normalized_not_rejected():
    meta, body = fm.parse("just a body\n")
    assert meta["title"] == "" and meta["status"] == "active" and meta["tags"] == []
    assert body == "just a body\n"
    raw, _ = fm.parse_raw("just a body\n")
    assert raw == {}


def test_normalize_tags_forms():
    assert fm.normalize_tags('"#a #b"') == ["#a", "#b"]
    assert fm.normalize_tags("#a, #b,#a") == ["#a", "#b"]
    assert fm.normalize_tags(["a", "#a", "", "b c"]) == ["#a"]  # "b c" is not a tag
    assert fm.normalize_tags(None) == [] and fm.normalize_tags(42) == []


def test_cli_columns_with_custom_separator():
    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "a.md")
        b = os.path.join(d, "b.md")
        with open(a, "w", encoding="utf-8") as f:
            f.write(LIST_TAGS)
        with open(b, "w", encoding="utf-8") as f:
            f.write("---\ntitle: old\nstatus: superseded\nsuperseded_by: 2026/09/new.md\n---\n")
        missing = os.path.join(d, "missing.md")
        proc = subprocess.run(
            [sys.executable, CLI, "--sep", "\x1f", "--fields", "status,superseded_by,title,tags", "--", a, b, missing],
            capture_output=True, text=True, check=True,
        )
        rows = [line.split("\x1f") for line in proc.stdout.splitlines()]
        assert rows[0] == [a, "active", "", "Quoted title", "#a #b #c"], rows[0]
        assert rows[1] == [b, "superseded", "2026/09/new.md", "old", ""], rows[1]
        assert rows[2] == [missing, "active", "", "", ""], rows[2]


STRICT_STRING_TAGS = STRING_TAGS.replace(
    "title: ADR: colon inside — unquoted", 'title: "ADR: colon inside — quoted"')


def test_unquoted_colon_title_is_tolerated_but_not_strict_yaml():
    # Real entries write `title: ADR: ...` unquoted. Strict YAML rejects that
    # ("mapping values are not allowed here"); this parser keeps the whole
    # remainder as the title, which is what every reader has always wanted.
    meta, _ = fm.parse(STRING_TAGS)
    assert meta["title"] == "ADR: colon inside — unquoted"
    try:
        import yaml  # type: ignore
    except ImportError:
        return
    try:
        yaml.safe_load(fm.split(STRING_TAGS)[0])
    except yaml.YAMLError:
        return
    raise AssertionError("expected strict YAML to reject the unquoted colon title")


def test_cross_check_with_pyyaml_when_available():
    try:
        import yaml  # type: ignore
    except ImportError:
        print("  (skip: PyYAML not installed — cross-check not run)")
        return
    for text in (STRICT_STRING_TAGS, LIST_TAGS, NESTED):
        block, _ = fm.split(text)
        ours = fm.parse_block(block)
        ref = yaml.safe_load(block) or {}
        # YAML types dates/datetimes/ints; we keep strings by design — compare
        # on ISO form (our `...Z` spelling normalized to `+00:00`).
        import datetime

        def norm(v):
            if isinstance(v, dict):
                return {k: norm(x) for k, x in v.items()}
            if isinstance(v, list):
                return [norm(x) for x in v]
            if isinstance(v, (datetime.date, datetime.datetime)):
                return v.isoformat()
            if v is None:
                return ""
            s = str(v)
            if len(s) == 10:  # a bare date stays a date on both sides
                return s
            try:
                return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
            except ValueError:
                return s
        assert norm(ours) == norm(ref), (norm(ours), norm(ref))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
