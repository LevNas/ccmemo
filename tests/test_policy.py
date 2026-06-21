#!/usr/bin/env python3
"""Dependency-free self-tests for hooks/lib redact & leak_scan (SPEC v1).

Run: python3 tests/test_policy.py   (exit 0 = all pass)

All secret-shaped values below are SYNTHETIC dummies — never real secrets —
per secrets-management.md (searching/embedding real values causes secondary
spread into transcripts and capture hooks).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from lib import leak_scan, redact  # noqa: E402

PH = redact.PLACEHOLDER

# Synthetic dummies (NOT real secrets).
DUMMY_OP = "op://Personal/some-item/field"
DUMMY_JWT = "eyJabcdefghijk.lmnopqrstuvwx"
DUMMY_PEM = "-----BEGIN RSA PRIVATE KEY-----"
DUMMY_GH = "ghp_" + "a" * 36
DUMMY_EMAIL = "someone@example.com"
NOREPLY_EMAIL = "1234+user@users.noreply.github.com"
GIT_SHA40 = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"  # 40 hex -> a hash
B64_SECRET = "AbCd1234EfGh5678IjKl9012MnOp3456QrSt7890"  # mixed 40 -> base64-ish


def test_redact_entry_masks_secrets():
    text = f"key={DUMMY_OP} jwt={DUMMY_JWT}\n{DUMMY_PEM}\ntok={DUMMY_GH}"
    out, hits = redact.redact_secrets_in_text(text)
    kinds = {k for k, _ in hits}
    assert kinds == {"op-ref", "jwt", "pem-private-key", "github-token"}, kinds
    assert DUMMY_OP not in out and DUMMY_GH not in out
    assert out.count(PH) == 4, out


def test_redact_entry_email_noreply_excluded():
    text = f"reach {DUMMY_EMAIL} or {NOREPLY_EMAIL}"
    out, hits = redact.redact_secrets_in_text(text)
    assert hits == [("email", 1)], hits
    assert DUMMY_EMAIL not in out
    assert NOREPLY_EMAIL in out, "noreply must survive"


def test_redact_entry_high_entropy_not_masked():
    # git SHA & base64-ish blobs must NOT be auto-redacted in entry bodies.
    text = f"commit {GIT_SHA40} blob {B64_SECRET}"
    out, hits = redact.redact_secrets_in_text(text)
    assert hits == [], hits
    assert GIT_SHA40 in out and B64_SECRET in out


def test_redact_args_structured():
    args = {
        "password": "hunter2",
        "name": "harmless",
        "nested": {"api_key": "x", "count": 5},
        "flag": True,
        "nothing": None,
    }
    out = redact.redact_args(args)
    assert out["password"] == PH
    assert out["name"] == "harmless"
    assert out["nested"]["api_key"] == PH
    assert out["nested"]["count"] == 5  # scalar passthrough
    assert out["flag"] is True and out["nothing"] is None


def test_redact_args_depth_failsafe():
    deep = cur = {}
    for _ in range(12):
        cur["child"] = {}
        cur = cur["child"]
    cur["leaf"] = "value"
    out = redact.redact_args(deep)
    # Somewhere past depth 8 the recursion bails to the placeholder.
    flat = repr(out)
    assert PH in flat, flat


def test_leak_scan_shapes():
    text = (
        "id 12345678-1234-1234-1234-123456789abc\n"
        "/home/realuser/project/file\n"
        "var ${CLAUDE_SESSION_ID} here\n"
        f"blob {B64_SECRET}\n"
        f"hash {GIT_SHA40}\n"
    )
    kinds = {f.kind for f in leak_scan.scan(text)}
    assert "uuid" in kinds
    assert "home-path" in kinds
    assert "unexpanded-placeholder" in kinds
    assert "base64-secret" in kinds  # mixed blob flagged
    # pure-hex git SHA must NOT be flagged as base64-secret
    sha_findings = [
        f for f in leak_scan.scan(f"hash {GIT_SHA40}") if f.kind == "base64-secret"
    ]
    assert sha_findings == [], sha_findings


def test_leak_scan_placeholdered_home_excluded():
    findings = leak_scan.scan("path /home/<user>/project/file")
    assert [f for f in findings if f.kind == "home-path"] == []


def test_leak_scan_repo_name_from_env():
    key = "CCMEMO_PRIVATE_REPO_NAMES"
    prev = os.environ.get(key)
    os.environ[key] = "my-private-brain, other-repo"
    try:
        findings = leak_scan.scan("see my-private-brain/.claude for details")
        assert any(f.kind == "private-repo-name" for f in findings), findings
        # unset -> no repo-name scanning
        os.environ[key] = ""
        findings2 = leak_scan.scan("see my-private-brain/.claude for details")
        assert [f for f in findings2 if f.kind == "private-repo-name"] == []
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


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
