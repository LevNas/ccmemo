#!/usr/bin/env python3
"""Dependency-free tests for hooks/postwrite_check_md_links.py link extraction."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from postwrite_check_md_links import extract_relative_links  # noqa: E402


def targets(text):
    return [(lineno, target) for lineno, _text, target, _a, _i in extract_relative_links(text)]


def test_plain_link_found():
    assert targets("see [a](a.md)\n") == [(1, "a.md")]


def test_link_in_inline_code_ignored():
    assert targets("write `[text](relative.md)` links\n") == []
    assert targets("double ``[a](b.md) with ` inside`` here\n") == []
    # the link after the span is still checked
    assert targets("`[x](x.md)` and [y](y.md)\n") == [(1, "y.md")]


def test_span_continues_onto_next_line():
    text = "inline: `- see: [t](YYYY/MM/\n<f>.md)` done\n[z](z.md)\n"
    assert targets(text) == [(3, "z.md")]


def test_span_does_not_cross_blank_line():
    # an unmatched backtick must not hide links in the next paragraph
    assert targets("a stray ` here\n\n[a](a.md) and ` there\n") == [(3, "a.md")]


def test_stray_backtick_in_tight_list_hides_nothing():
    # a span continues onto one more line at most, so the stray backtick on
    # line 1 cannot pair with the one on line 3 and hide the link on line 2
    text = "- a ` stray\n- [b](b.md)\n- `code` and [c](c.md)\n"
    assert targets(text) == [(2, "b.md"), (3, "c.md")]


def test_fenced_blocks_ignored_and_line_numbers_kept():
    text = "```bash\n[a](a.md)\n\n`x\n```\n~~~\n[b](b.md)\n~~~\n[c](c.md)\n"
    assert targets(text) == [(9, "c.md")]


def test_external_and_anchor_links_skipped():
    assert targets("[a](https://x/y) [b](#top) [c](c.md#sec)\n") == [(1, "c.md")]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
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
