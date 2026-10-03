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

Entry profile (1.30.3, #63). Run over a real knowledge base, the scan flagged
every entry, almost all of it noise. These are not findings:
    * the frontmatter ``id:`` UUID -- the entry's own identifier;
    * a private repo name inside that same repository -- callers pass
      ``own_repo`` (the name only leaks when the text leaves the repository);
    * ``${VAR}`` inside inline code or a fenced block -- documentation of a
      variable, not a template that failed to expand;
    * word-like tokens that only look high-entropy because ``/`` joins them
      (relative links between entries, URL paths, ENV_NAME=). See
      ``_looks_like_base64_secret``.
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
FRONTMATTER_ID = re.compile(r"^id:\s*[\"']?[0-9a-f-]{36}[\"']?\s*$", re.IGNORECASE)
INLINE_CODE = re.compile(r"`[^`]*`")
FENCE = re.compile(r"^\s*(```|~~~)")

# Below this many character-class runs per letter/digit, a token reads as
# words. Calibrated 2026-10-03 (#63): 833 of 837 mixed-case path tokens from a
# real knowledge base and its task notes fall below it; of 45,000 random
# base64 / base64url tokens (30-69 bytes), 2 do (random minimum 0.286).
WORD_LIKE_RUN_RATIO = 0.3

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


def _class_run_ratio(token: str) -> float:
    """Runs of the same class (lower / upper / digit) per letter or digit.

    Words keep a class for several characters (low ratio); random base64
    switches about every 1.6 characters (around 0.6).
    """
    classes = ["l" if c.islower() else "u" if c.isupper() else "d"
               for c in token if c.isalnum()]
    if not classes:
        return 0.0
    runs = 1 + sum(1 for a, b in zip(classes, classes[1:]) if a != b)
    return runs / len(classes)


def _looks_like_base64_secret(token: str) -> bool:
    """Whether a high-entropy match is a base64-ish secret rather than a hash.

    Pure hex (git SHA, sha256, blob ids) is excluded; we only flag tokens that
    carry base64 alphabet signals (+, /, =) or a mixed upper/lower/digit shape.
    """
    if re.fullmatch(r"[0-9a-f]+", token, re.IGNORECASE):
        return False  # pure hex -> almost certainly a hash / SHA
    if not any(c.isupper() for c in token) and not any(c in token for c in "+="):
        # Lower case, digits and / only: a path such as an entry link. Random
        # base64 of 40+ characters has no upper case with odds of about 1e-9.
        return False
    if _class_run_ratio(token) < WORD_LIKE_RUN_RATIO:
        return False  # words joined by / - _ (URL paths, ENV_NAME=)
    has_b64_signal = any(c in token for c in "+/=")
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_digit = any(c.isdigit() for c in token)
    return has_b64_signal or (has_upper and has_lower and has_digit)


def scan(text: str, own_repo: str | None = None) -> list[Finding]:
    """Scan entry text for leak-prone shapes. Returns advisory findings.

    ``own_repo`` is the name of the repository the text lives in; a private
    repo name equal to it is not reported (compared case-insensitively).
    """
    findings: list[Finding] = []
    repo_pattern = _private_repo_pattern()
    own = (own_repo or "").lower()
    lines = text.splitlines()
    # Frontmatter only when the leading --- is closed; a lone opening line
    # (a horizontal rule) must not hide the rest of the file.
    fm_end = 0
    if lines and lines[0].strip() == "---":
        fm_end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
    in_fence = False

    for idx, line in enumerate(lines):
        lineno = idx + 1
        in_frontmatter = idx <= fm_end and fm_end > 0
        if not in_frontmatter and FENCE.match(line):
            in_fence = not in_fence

        for m in UUID.finditer(line):
            if in_frontmatter and FRONTMATTER_ID.match(line):
                continue  # the entry's own identifier
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

        code_spans = [s.span() for s in INLINE_CODE.finditer(line)]
        for m in UNEXPANDED_PLACEHOLDER.finditer(line):
            if in_fence or any(a <= m.start() < b for a, b in code_spans):
                continue  # a variable written about, not a failed expansion
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
                if own and m.group(0).lower() == own:
                    continue  # the repository's own name, inside it
                findings.append(
                    Finding(
                        lineno,
                        "private-repo-name",
                        m.group(0),
                        "private/メインブレイン repo 名。プレースホルダ化を検討。",
                    )
                )

    return findings
