"""Redaction per the shared redact/leak-scan SPEC v1 (Python implementation).

Mirrors the ccgate ``policy-core/redact.ts``母体 so structured data and free
text are masked the same way on both sides of the language boundary. The two
implementations share the SPEC, not the code.

Two surfaces:
    redact_args / redact_free_text  -- structured tool args & opaque free text
                                       (faithful port of the TS母体)
    redact_secrets_in_text          -- the "entry profile": mask only the
                                       unambiguous secret *value* patterns in
                                       human-readable knowledge entry bodies,
                                       deliberately excluding the high-entropy
                                       pattern (it false-positives on git SHAs,
                                       long paths, base64 examples).
"""

from __future__ import annotations

import re

# U+2039 / U+203A single guillemets — identical to the TS母体 placeholder so
# redacted output is byte-for-byte comparable across implementations.
PLACEHOLDER = "‹redacted›"

# Recursion fail-safe depth for structured data (matches TS母体: depth > 8).
MAX_DEPTH = 8

# --- Sensitive key names (substring match, lower-cased) -----------------------
# Ported 1:1 from the TS母体 (includes the extra `privatekey` it carries).
SENSITIVE_KEYS = (
    "password",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "privatekey",
    "client_secret",
    "access_key",
    "session",
    "cookie",
)

# --- Value patterns (regex literals ported verbatim from the TS母体) ----------
OP_REF = re.compile(r"\bop://\S+", re.IGNORECASE)
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
GH_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b")

# Structured-data value patterns: the full SPEC set incl. high-entropy.
_STRUCTURED_VALUE_PATTERNS = (OP_REF, JWT, PEM, GH_TOKEN, EMAIL, HIGH_ENTROPY)

# Entry-body secret patterns: unambiguous secrets only — high-entropy is
# intentionally absent (see module docstring). Each entry is (kind, pattern).
ENTRY_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("op-ref", OP_REF),
    ("jwt", JWT),
    ("pem-private-key", PEM),
    ("github-token", GH_TOKEN),
    ("email", EMAIL),
)


def _is_noreply(email: str) -> bool:
    """Whether an email is a noreply form that secrets policy treats as benign.

    secrets-management.md forbids personal emails *other than* noreply forms,
    so masking these would needlessly break legitimate author attributions.
    """
    lowered = email.lower()
    return "noreply" in lowered or lowered.endswith("users.noreply.github.com")


def redact_string(value: str) -> str:
    """Mask any secret *value* pattern found in a single string (structured use).

    Faithful to the TS母体 redactString: applies the full SPEC value-pattern set
    including high-entropy. Use for opaque structured values, NOT entry bodies.
    """
    redacted = value
    for pattern in _STRUCTURED_VALUE_PATTERNS:
        redacted = pattern.sub(PLACEHOLDER, redacted)
    return redacted


def redact_args(value, depth: int = 0):
    """Recursively redact structured data: keep keys, mask sensitive values.

    Mirrors the TS母体 redactArgs:
      * sensitive key (substring, lower-cased) -> value replaced wholesale
      * strings -> redact_string (value-pattern masking)
      * scalars (int/float/bool/None) -> passed through untouched
      * depth > MAX_DEPTH -> PLACEHOLDER (fail-safe)
      * anything else (callables, etc.) -> PLACEHOLDER
    """
    if depth > MAX_DEPTH:
        return PLACEHOLDER
    if isinstance(value, str):
        return redact_string(value)
    # bool is a subclass of int — both are scalars passed through.
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            key_l = str(key).lower()
            if any(s in key_l for s in SENSITIVE_KEYS):
                out[key] = PLACEHOLDER
            else:
                out[key] = redact_args(val, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_args(v, depth + 1) for v in value]
    return PLACEHOLDER


def redact_free_text(text: str, mode: str = "length") -> str:
    """Redact opaque free text. Default fail-safe drops everything but length.

    Mirrors the TS母体 redactFreeText. mode="pattern" masks only high-entropy
    and op:// fragments (partial); default "length" is the safe full-redact.
    """
    if mode == "pattern":
        masked = HIGH_ENTROPY.sub(PLACEHOLDER, text)
        masked = OP_REF.sub(PLACEHOLDER, masked)
        return masked
    return f"{PLACEHOLDER} len={len(text)}"


def redact_secrets_in_text(text: str) -> tuple[str, list[tuple[str, int]]]:
    """Mask unambiguous secret *values* in a knowledge entry body.

    The entry profile: only the SPEC patterns that are unambiguous secrets
    (op://, JWT, PEM, GitHub token, non-noreply email). High-entropy is
    excluded to avoid clobbering git SHAs / long paths / base64 examples.

    Returns (new_text, hits) where hits is a list of (kind, count). When no
    secret is found, new_text is the input unchanged and hits is empty.
    """
    hits: list[tuple[str, int]] = []
    result = text
    for kind, pattern in ENTRY_SECRET_PATTERNS:
        if kind == "email":
            count = 0

            def _mask_email(m: re.Match[str]) -> str:
                nonlocal count
                if _is_noreply(m.group(0)):
                    return m.group(0)
                count += 1
                return PLACEHOLDER

            result = pattern.sub(_mask_email, result)
            if count:
                hits.append((kind, count))
        else:
            new_result, n = pattern.subn(PLACEHOLDER, result)
            if n:
                hits.append((kind, n))
                result = new_result
    return result, hits
