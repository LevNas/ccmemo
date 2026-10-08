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


# Unquoted values: (value, a strict YAML reader rejects it or reads it differently).
HAZARD_CASES = [
    ("ADR: colon inside", True), ("ends with colon:", True), ("issue #12 fix", True),
    ("`code` first", True), ("#a #b", True), ("&anchor", True), ("!tag", True),
    ("| block", True), ("@at", True), ("- dash space", True), ("[WIP] title", True),
    ("!!str x", True), ("[a, b]  # note", True), ("{by: x, at: y} # c", True),
    ("[#a, #b]", True), ("{by: x: y, at: z}", True), ('"a"  # c', True), ('"say "hi""', True),
    ('"C:\\path"', True), ('"tab\\tx"', True), ("'it's'", True), ('"unclosed', True),
    ("[don't, won't]", True), ("# none", True), ("[:]", True), ("[-]", False),
    ("[a]x", True), ("[[a]]", False),
    # extra text after a closed collection, also when it ends with a bracket
    ("[a, b]]", True), ("[a][b]", True), ("{a: b}}", True), ("[a, b]#note", True),
    # a comment without a space after "#" is still a comment to YAML
    ("[a, b] #note", True), ("{a: b} #note", True),
    # a quote inside an unquoted item is text, and both readers split alike
    ("[x, it's]", False), ("[don't]", False), ("[a \"b\" c]", False),
    ("{a: it's}", False), ("{a: 'x, y'}", False), ('{a: "b, c"}', False),
    ("[it's, 'x, y']", True), ("{a: it's, b: won't}", True),
    # quoted items inside nested collections are read alike by both
    ('[["a,b"], c]', False), ("[[',']]", False), ("{a: ['x,y']}", False), ("[{a: 'x,y'}]", False),
    # a quoted key keeps its quotes here; the cause is the key
    ('{"a": "x, y"}', True), ("{'a': 'x, y'}", True), ('{"a": b}', True),
    ('{"a, b": c}', True), ('{"a #b": c}', True), ('{"a: b": c}', True),
    ('{"-a": c}', True), ('{"[a]": c}', True),
    # a comment inside the brackets cuts the item and the closing bracket
    ("[a, b # note]", True), ("{a: b # x}", True),
    # a bracket inside a quoted item does not close the collection
    ('["a]"]', False), ("['x]']", False), ("[a, 'b]']", False),
    ("x  # real comment", False), ("plain words", False), ("https://example.com/a#frag", False),
    ("human:x", False), ("2026/09/x.md", False), ("-dash", False), ("C# notes", False),
    ("a#b c", False), ("100% done", False), ("[a, b]", False), ("{k: v}", False),
    ('"quoted: ok #1"', False), ("'single # ok'", False), ('["#a", \'#b\']', False),
    ('"esc \\" ok"', False), ("'it''s'", False), ("2026-09-24T10:00:00+09:00", False),
]

# Whole blocks: (block, key paths yaml_hazards reports).
HAZARD_BLOCKS = [
    # a block scalar is one finding; its lines are not read as keys
    ("description: |\n  see: x: y\n  more #1\nstatus: active\n", ["description"]),
    ("description: >-\n  folded\nstatus: active\n", ["description"]),
    # a quoted value continued on the next line: one finding, not one per line
    ('description: "multi\n  line: x #1"\nstatus: active\n', ["description"]),
    # same-indent sequence under a key
    ("tags:\n- x: y: z\n- b\n", ["tags.x"]),
    ("tags:\n- \"#a\"\n- '#b'\n", []),
    ("verified:\n  - by: human:x\n    at: 2026-09-24T10:00:00Z\n  - {by: process:n, at: 2026-09-25}\n", []),
    ("superseded_by: 2026/09/x.md   # trailing comment\nstale_after: 2026-12-01\n", []),
    ("title: ok\r\nnote: a: b\r\n", ["note"]),
    # a hand-wrapped plain value: this parser drops the rest, status included
    ("description: Open this when the hook misfires\n  and the log shows X\nstatus: active\n",
     ["description"]),
    ("verified:\n  - by: x\n      more\n    at: y\n", ["verified.by"]),
    # a flagged value spans only its own lines, not the sibling keys of its item
    ('verified:\n  - by: "x" # who\n    at: a: b\n', ["verified.by", "verified.at"]),
    ("verified:\n  - nested:\n      c: d: e\n", ["verified.nested.c"]),
    ("tags: [a,\n  b]\nstatus: x\n", ["tags"]),
    # "-" alone: valid, its content follows on deeper lines (once crashed)
    ("tags:\n  -\n    a: b\n", []),
    ("tags:\n  -\n", []),
    # the lines of an unclosed flow mapping are not read as keys
    ("v: {by: x,\n  at: y: z}\nstatus: a\n", ["v"]),
    # a quoted value wrapped onto the next line: quoting alone does not fix it
    ('description: "Open when: the hook fails"\n  and log shows X\nstatus: active\n',
     ["description"]),
    ("description: Open when: the hook fails\n  and log shows X\nstatus: active\n",
     ["description", "description"]),
    # a line that is neither a key nor an item, at the key column
    ("verified:\n  - by: x\n    cont\n    at: y\n", ["verified.by"]),
    ("tags: [{a: b, c: d}]\n", ["tags"]),
    ("tags: [a: b]\n", ["tags"]),
    # several spaces after "-": the keys below sit two columns further right
    ("verified:\n  -   by: x\n      at: y\n", ["verified.by"]),
    ("verified:\n  -   by: x\n    at: y\n", ["verified.by"]),
    ("verified:\n  -   by: x\n      at: a: b\n", ["verified.by", "verified.at"]),
    ("verified:\n  -   by: x\n", []),
    ("verified:\n  - by: x\n    at: y\n", []),
    # a block list inside a list item: one finding per key
    ("v:\n  - -\n  - - a\n", ["v"]),
    ("v:\n  -\n    - a\n  -\n    - b\n", []),
    ("v:\n  - by: - a\n", ["v.by"]),
    # the deeper lines of a nested list belong to it: one finding
    ("v:\n  - - a\n    - b\n", ["v"]),
    # one finding per key, not one for the whole block
    ("v:\n  - - a\nw:\n  - - b\n", ["v", "w"]),
    # several spaces after "-": only a next key where the item's keys are expected
    ("v:\n  -   a:\n        b: c\n", []),
    ("v:\n  -  a:\n       - x\n", []),
    ("v:\n  -   by:\n      - a\n", []),
    # ... but a sibling key after the key's own nested value is reported: this
    # parser drops it, YAML reads it as a key of the item
    ("v:\n  -   a:\n        b: c\n      d: e\n", ["v.a"]),
    ("v:\n  -   by:\n      - a\n      at: y\n", ["v.by"]),
    ("v:\n  -    a:\n         - x\n       d: e\n", ["v.a"]),
    ("v:\n  -     a:\n          b:\n            c: d\n        e: f\n", ["v.a"]),
    ("v:\n  -   a:\n        b: c\n      d: e: f\n", ["v.a", "v.d"]),
    # an unclosed flow collection owns the deeper lines that follow
    ("tags: ['a, b]\n  c]\nstatus: x\n", ["tags"]),
    ("k: [[a, b]\n  c]\nstatus: x\n", ["k"]),
    # several spaces before a plain item are read the same by both
    ("v:\n  -   a\n  -   b\n", []),
]


def _norm(v):
    """YAML types dates and nulls; this parser keeps strings. Compare on text."""
    import datetime
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm(x) for x in v]
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


def test_yaml_hazards_flags_only_values_strict_yaml_misreads():
    for value, hazard in HAZARD_CASES:
        found = fm.yaml_hazards(f"title: {value}\n")
        assert bool(found) == hazard, (value, found)
        if hazard:
            assert found[0][0] == "title", found
    # nested keys and list items get a dotted path; the safe fixtures are clean
    block = ("tags:\n  - \"#a\"\n  - #b\n"
             "verified:\n  - by: human:x\n    at: 2026-09-24T10:00:00Z\n  - by: a: b\n"
             "generated:\n  by: claude-code\n  at: issue #3\n")
    assert [k for k, _ in fm.yaml_hazards(block)] == ["tags", "verified.by", "generated.at"], \
        fm.yaml_hazards(block)
    for text in (STRICT_STRING_TAGS, LIST_TAGS, NESTED):
        assert fm.yaml_hazards(fm.split(text)[0]) == [], text
    assert [k for k, _ in fm.yaml_hazards(fm.split(STRING_TAGS)[0])] == ["title"]
    for block, paths in HAZARD_BLOCKS:
        assert [k for k, _ in fm.yaml_hazards(block)] == paths, (block, fm.yaml_hazards(block))
    # each reason names its fix; quoting is not the fix for every case
    reason = fm.yaml_hazards("tags: [a, b]  # note\n")[0][1]
    assert "comment to its own line" in reason, reason
    reason = fm.yaml_hazards("superseded_by: # none\n")[0][1]
    assert "delete the comment" in reason, reason
    for block, fix in (("tags: [{a: b, c: d}]\n", "flatten it"),
                       ("title: [a]x\n", "remove the text"),
                       ("tags: [a: b]\n", "as a mapping")):
        reason = fm.yaml_hazards(block)[0][1]
        assert fix in reason, (block, reason)
    # out of scope by design: YAML's typed scalars (documented, not reported)
    for value in ("yes", "null", "~", "0x1F"):
        assert fm.yaml_hazards(f"status: {value}\n") == [], value
    # the lenient reading itself is unchanged
    assert fm.parse("---\ntitle: issue #12 fix\n---\n")[0]["title"] == "issue #12 fix"


def _quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _quote_fixed_blocks():
    """For each case whose advice is "quote the value", the block after quoting."""
    out = []
    for value, _ in HAZARD_CASES:
        found = fm.yaml_hazards(f"title: {value}\n")
        if found and found[0][1].endswith("quote the value"):
            out.append((value, f"title: {_quoted(value)}\n"))
    return out


def test_following_the_quote_advice_clears_the_finding():
    fixed = _quote_fixed_blocks()
    assert len(fixed) >= 10, fixed
    for value, block in fixed:
        assert fm.yaml_hazards(block) == [], (value, block, fm.yaml_hazards(block))
        assert fm.parse_block(block)["title"] == value, (value, fm.parse_block(block))


def test_yaml_hazards_agree_with_pyyaml_when_available():
    try:
        import yaml  # type: ignore
    except ImportError:
        print("  (skip: PyYAML not installed — cross-check not run)")
        return
    # The function's answer itself (not the labels above), on every case in this
    # file: a hazard is reported when PyYAML rejects the block or reads it
    # differently, and not otherwise. This covers the listed forms only; typed
    # scalars (checked above) are out of scope.
    blocks = [f"title: {v}\n" for v, _ in HAZARD_CASES]
    blocks += [b.replace("\r", "") for b, _ in HAZARD_BLOCKS]
    for block in blocks:
        try:
            ref = yaml.safe_load(block)
            same = isinstance(ref, dict) and _norm(ref) == _norm(fm.parse_block(block))
        except yaml.YAMLError:
            same = False
        found = fm.yaml_hazards(block)
        assert bool(found) != same, (block, found, "PyYAML reads the same" if same else "PyYAML differs")
    # after following the "quote the value" advice, both readers agree
    for value, block in _quote_fixed_blocks():
        assert yaml.safe_load(block)["title"] == value, (value, block)


# (block, fixed block, words the advice must contain): the advice is followed
# literally and the result is checked, not the wording alone.
ADVICE_CASES = [
    ("tags: [a, b] #note\n", "# note\ntags: [a, b]\n", "comment to its own line"),
    ("tags: [a, b]]\n", "tags: [a, b]\n", "remove the text"),
    ("tags: [a][b]\n", "tags: [a, b]\n", "remove the text"),
    ("v: {a: b}}\n", "v: {a: b}\n", "remove the text"),
    ("tags: [don't, won't]\n", 'tags: ["don\'t", "won\'t"]\n', "quote that item"),
    ("verified:\n  -   by: x\n      at: y\n", "verified:\n  - by: x\n    at: y\n",
     "one space after"),
    ("v:\n  - - a\n  - - b\n", "v:\n  -\n    - a\n  -\n    - b\n", "own lines"),
    ("v:\n  - -\n  - - a\n", "v:\n  -\n    -\n  -\n    - a\n", "own lines"),
    # a nested list written over several lines: one finding, not a second
    # "continues on a deeper line" whose advice would turn the list into a string
    ("v:\n  - - a\n    - b\n", "v:\n  -\n    - a\n    - b\n", "own lines"),
    # the advice is not "[a, b]": that would turn a mapping into a pair
    ("v:\n  - - a: b\n", "v:\n  -\n    - a: b\n", "own lines"),
    # a quoted key: the cause is the key, quoting the item would change the value
    ('k: {"a": "x, y"}\n', 'k: {a: "x, y"}\n', "without quotes"),
    ("k: {'a': 'x, y'}\n", 'k: {a: "x, y"}\n', "without quotes"),
    ('k: {"a": b}\n', "k: {a: b}\n", "without quotes"),
    ('k: {"a b": c}\n', "k: {a b: c}\n", "without quotes"),
    # a sibling key after the key's own nested value
    ("v:\n  -   a:\n        b: c\n      d: e\n", "v:\n  - a:\n      b: c\n    d: e\n",
     "one space after"),
    ("v:\n  -   by:\n      - a\n      at: y\n", "v:\n  - by:\n      - a\n    at: y\n",
     "one space after"),
    ("k: [a, b # note]\n", 'k: [a, "b # note"]\n', "quote the item"),
]


def test_advice_for_the_reported_gaps_fixes_them():
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
        print("  (skip: PyYAML not installed — only the stdlib steps run)")
    for block, fixed, words in ADVICE_CASES:
        found = fm.yaml_hazards(block)
        assert len(found) == 1, (block, found)
        assert words in found[0][1], (block, found)
        # following the advice clears the finding ...
        assert fm.yaml_hazards(fixed) == [], (block, fixed, fm.yaml_hazards(fixed))
        # ... and the data is the same list or mapping, not a string
        fixed_meta = fm.parse_block(fixed)
        assert not any(isinstance(v, str) and v and v[0] in "[{-" for v in fixed_meta.values()), fixed_meta
        # ... and PyYAML reads what this parser reads
        if yaml is not None:
            assert _norm(yaml.safe_load(fixed)) == _norm(fixed_meta), (fixed, yaml.safe_load(fixed), fixed_meta)
    # quoting a flow collection or a nested list, as the old advice said, would
    # have made the data a string: it is not the advice for these
    assert "quote the value" not in fm.yaml_hazards("tags: [a, b] #note\n")[0][1]
    assert "quote the value" not in fm.yaml_hazards("v:\n  - - a\n")[0][1]
    assert "quote that item" not in fm.yaml_hazards('k: {"a": "x, y"}\n')[0][1]


def test_quoted_key_that_cannot_be_unquoted_gets_no_misleading_advice():
    # taking the quotes off these keys changes the data (a key split at the
    # comma) or leaves the block rejected, so the reason must not say to do it
    for key in ("a, b", "a #b", "a: b", "-a", "[a]", "a{b}", " a", ""):
        for quote in ('"', "'"):
            block = f"k: {{{quote}{key}{quote}: c}}\n"
            found = fm.yaml_hazards(block)
            assert len(found) == 1, (block, found)
            assert "without quotes" not in found[0][1], (block, found)
            assert "rename the key" in found[0][1], (block, found)


def test_comment_needs_a_space_before_the_hash():
    # "#" after a space is a comment even without a space after it; without a
    # space before it, it is text after the collection
    assert "comment" in fm.yaml_hazards("tags: [a, b] #note\n")[0][1]
    assert "comment" in fm.yaml_hazards("tags: [a, b]   #\n")[0][1]
    assert "text after" in fm.yaml_hazards("tags: [a, b]#note\n")[0][1]


def test_docstring_lists_the_forms_of_the_gaps():
    doc = fm.yaml_hazards.__doc__
    for form in ("[a, b]]", "[a][b]", "[don't, won't]", "[x, it's]", "- - a",
                 "several spaces", "_MAX_FLOW_DEPTH", "[a, b # c]", '{"a": b}',
                 "raise on any ``str`` input", "not a proof"):
        assert form in doc, form
    assert "Never raises" not in doc


def test_flow_nesting_is_capped_and_nothing_raises():
    depth = fm._MAX_FLOW_DEPTH
    ok = "k: " + "[" * depth + "]" * depth + "\n"
    assert fm.yaml_hazards(ok) == [], fm.yaml_hazards(ok)
    for n in (depth + 1, 600, 5000):
        found = fm.yaml_hazards("k: " + "[" * n + "]" * n + "\n")
        assert [k for k, _ in found] == ["k"] and "nested more than" in found[0][1], (n, found)
        found = fm.yaml_hazards("k: " + "{a: " * n + "x" + "}" * n + "\n")
        assert [k for k, _ in found] == ["k"], (n, found)
    # yaml_hazards returns a list of (str, str) for the str inputs tried here
    # (load_graph runs it on every entry): brackets, quotes, colons, dashes,
    # hashes, tabs, carriage returns and indentation, alone or as a value
    import random
    rng = random.Random(72)
    alphabet = ["[", "]", "{", "}", "'", '"', ":", ": ", "-", "- ", ",", " #", "#", " ", "  ",
                "\n", "a", "|", "\\", "\t", "\r", "\r\n"]
    for i in range(6000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        if i % 2:
            text = "k: " + text
        found = fm.yaml_hazards(text)
        assert isinstance(found, list), text
        assert all(isinstance(x, tuple) and len(x) == 2 and all(isinstance(y, str) for y in x)
                   for x in found), (text, found)
    deep = "k: " + "[" * 3000 + "'" + "]" * 3000 + "\n- " * 500
    assert isinstance(fm.yaml_hazards(deep), list)


def test_many_quote_characters_in_a_flow_item_stay_fast():
    import time
    start = time.perf_counter()
    found = fm.yaml_hazards("k: [a" + "'" * 60000 + "]\n")
    assert isinstance(found, list)
    assert time.perf_counter() - start < 1.0, "quadratic in the number of quotes"


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
