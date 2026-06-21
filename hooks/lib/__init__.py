"""Shared validators for ccmemo hooks.

A small, dependency-free Python implementation of the shared redact /
leak-scan SPEC (the canonical spec lives alongside the ccgate
``policy-core`` TypeScript implementation). The two implementations are
deliberately *not* coupled across the language boundary — they each
implement the same SPEC independently.

Modules:
    redact     -- mask secret *values* (op://, JWT, PEM, GitHub token, email, ...)
    leak_scan  -- detect leak-prone *shapes* (UUID, home-path, repo name, ${...})
"""
