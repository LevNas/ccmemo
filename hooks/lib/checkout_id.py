"""Opaque per-checkout identifier for capture filenames (issue #24).

In git-tracked mode every checkout of a repository (linked worktrees,
clones on other machines) appends to a same-named ``context-*.md``
capture file, and the copies diverge. With
``CCMEMO_CAPTURE_CHECKOUT_SUFFIX=1`` the context writer ends capture
filenames in a checkout identifier so copies never share a name across
checkouts.

The identifier is the first 8 hex digits of
``sha256(hostname + NUL + realpath(git toplevel))``; outside a git
repository the realpath of the working directory stands in for the
toplevel. It is a one-way digest on purpose: capture files get committed
to possibly public repositories, so neither the hostname nor the path may
appear in a filename, file body or log line — only the digest leaves this
module.

Opt-in and off by default: without the variable, filenames and the reuse
rule stay exactly as before.
"""

import hashlib
import os
import socket
import subprocess

ID_LENGTH = 8


def suffix_enabled() -> bool:
    """Return True when checkout-suffixed capture filenames are opted in."""
    return os.environ.get("CCMEMO_CAPTURE_CHECKOUT_SUFFIX") == "1"


def _checkout_root(path: str) -> str:
    """Return the realpath of the git toplevel containing *path*, or of
    *path* itself outside a git repository (or when git is unavailable)."""
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return os.path.realpath(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.path.realpath(path)


def derive_id(hostname: str, root: str) -> str:
    """Pure digest step, split out so tests can pin it with fictional values."""
    material = hostname.encode("utf-8") + b"\0" + root.encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:ID_LENGTH]


def checkout_id(path: str) -> str:
    """Return the 8-hex identifier of the checkout containing *path*."""
    return derive_id(socket.gethostname(), _checkout_root(path))
