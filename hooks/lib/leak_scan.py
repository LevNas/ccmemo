"""Leak-scan per the shared redact/leak-scan SPEC v1 (Python implementation).

leak-scan is the record/commit-time *gate* that detects leak-prone *shapes*
(as opposed to redact, which masks secret *values*). It is the part the SPEC
keeps OUT of ccgate's policy-core — it belongs to the record / pre-commit
layer. Findings are advisory: the caller decides whether to warn or block.

Detected shapes:
    uuid                  -- UUID v4-shaped strings (session ids, etc.)
    home-path             -- /home/<user>/ exposing a real username
    unexpanded-placeholder-- ${VAR} that a template forgot to expand
                             (the ${CLAUDE_SESSION_ID} -> real-id class of bug)
    base64-secret         -- high-entropy base64-ish blobs (pure hex excluded
                             so git SHAs / sha256 don't false-positive)
    private-repo-name     -- main-brain / private repo names, supplied at
                             RUNTIME via $CCMEMO_PRIVATE_REPO_NAMES so the name
                             is never baked into this public source file
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
HOME_PATH = re.compile(r"/home/([^/\s]+)/")
UNEXPANDED_PLACEHOLDER = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b")

# Names that, when present, indicate a private repo name leaked into a public
# entry. Sourced at runtime so no private name is ever committed here.
_PRIVATE_REPO_ENV = "CCMEMO_PRIVATE_REPO_NAMES"


@dataclass
class Finding:
    lineno: int
    kind: str
    snippet: str
    suggestion: str


def _private_repo_pattern() -> re.Pattern[str] | None:
    """Build the private-repo-name pattern from the environment, or None.

    Dynamic generation keeps the actual private names out of this public file
    (the self-block avoidance / externalization principle in
    secrets-management.md). Empty/unset env -> no scanning for repo names.
    """
    raw = os.environ.get(_PRIVATE_REPO_ENV, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return None
    alternation = "|".join(re.escape(n) for n in names)
    return re.compile(rf"\b({alternation})\b", re.IGNORECASE)


def _looks_like_base64_secret(token: str) -> bool:
    """Whether a high-entropy match is a base64-ish secret rather than a hash.

    Pure hex (git SHA, sha256, blob ids) is excluded; we only flag tokens that
    carry base64 alphabet signals (+, /, =) or a mixed upper/lower/digit shape.
    """
    if re.fullmatch(r"[0-9a-f]+", token, re.IGNORECASE):
        return False  # pure hex -> almost certainly a hash / SHA
    has_b64_signal = any(c in token for c in "+/=")
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_digit = any(c.isdigit() for c in token)
    return has_b64_signal or (has_upper and has_lower and has_digit)


def scan(text: str) -> list[Finding]:
    """Scan entry text for leak-prone shapes. Returns advisory findings."""
    findings: list[Finding] = []
    repo_pattern = _private_repo_pattern()

    for idx, line in enumerate(text.splitlines()):
        lineno = idx + 1

        for m in UUID.finditer(line):
            findings.append(
                Finding(
                    lineno,
                    "uuid",
                    m.group(0),
                    "session id 等の UUID は記録不要。除去するか目的を確認。",
                )
            )

        for m in HOME_PATH.finditer(line):
            user = m.group(1)
            if user.startswith("<") and user.endswith(">"):
                continue  # already placeholdered (e.g. /home/<user>/)
            findings.append(
                Finding(
                    lineno,
                    "home-path",
                    m.group(0),
                    "ユーザー名露出。/home/<user>/ へプレースホルダ化。",
                )
            )

        for m in UNEXPANDED_PLACEHOLDER.finditer(line):
            findings.append(
                Finding(
                    lineno,
                    "unexpanded-placeholder",
                    m.group(0),
                    "未展開のテンプレ変数。展開漏れ（実ID混入）でないか確認。",
                )
            )

        for m in HIGH_ENTROPY.finditer(line):
            token = m.group(0)
            if _looks_like_base64_secret(token):
                findings.append(
                    Finding(
                        lineno,
                        "base64-secret",
                        token[:12] + "…",
                        "高エントロピー文字列。秘密値なら除去・伏せ字を確認。",
                    )
                )

        if repo_pattern is not None:
            for m in repo_pattern.finditer(line):
                findings.append(
                    Finding(
                        lineno,
                        "private-repo-name",
                        m.group(0),
                        "private/メインブレイン repo 名。プレースホルダ化を検討。",
                    )
                )

    return findings
