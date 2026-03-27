#!/usr/bin/env python3
"""Regenerate the tag registry in .claude/knowledge/CLAUDE.md.

Scans all entries for tags in YAML frontmatter and rebuilds the tag
registry section, preserving existing descriptions where possible.

Usage:
    python3 regenerate-tag-registry.py [knowledge_dir]
    python3 regenerate-tag-registry.py --write       # update CLAUDE.md in place
    python3 regenerate-tag-registry.py --dry-run     # preview (default)
"""

import os
import re
import sys
from pathlib import Path


def extract_tags_from_entry(filepath: Path) -> list[str]:
    """Extract tags from a single entry's YAML frontmatter."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except OSError:
        return []

    # Find YAML frontmatter
    if not content.startswith("---"):
        return []
    end = content.find("---", 3)
    if end == -1:
        return []
    frontmatter = content[3:end]

    # Extract tags line
    for line in frontmatter.splitlines():
        if line.strip().startswith("tags:"):
            tags_str = line.split(":", 1)[1].strip().strip('"').strip("'")
            return re.findall(r"#[a-z][a-z0-9-]*", tags_str)

    return []


def scan_entries(entries_dir: Path) -> dict[str, int]:
    """Scan all entries and return tag -> count mapping."""
    tag_counts: dict[str, int] = {}
    for entry in entries_dir.rglob("*.md"):
        if entry.name == "CLAUDE.md":
            continue
        for tag in extract_tags_from_entry(entry):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return tag_counts


def parse_existing_registry(claude_md: Path) -> dict[str, str]:
    """Parse existing tag registry to preserve descriptions.

    Returns tag -> description mapping.
    """
    descriptions: dict[str, str] = {}
    if not claude_md.is_file():
        return descriptions

    try:
        content = claude_md.read_text(encoding="utf-8")
    except OSError:
        return descriptions

    # Match lines like: - #tag — description
    # or: `#tag`
    for line in content.splitlines():
        # Format: - #tag — description
        match = re.match(r"^-\s+(#[a-z][a-z0-9-]*)\s+—\s+(.+)$", line.strip())
        if match:
            descriptions[match.group(1)] = match.group(2)
            continue
        # Format: `#tag`
        match = re.match(r"^`(#[a-z][a-z0-9-]*)`$", line.strip())
        if match:
            descriptions[match.group(1)] = ""

    return descriptions


def format_registry(
    tag_counts: dict[str, int], descriptions: dict[str, str]
) -> str:
    """Format the tag registry section."""
    lines = []
    # Sort by count (descending), then alphabetically
    sorted_tags = sorted(tag_counts.keys(), key=lambda t: (-tag_counts[t], t))

    for tag in sorted_tags:
        count = tag_counts[tag]
        desc = descriptions.get(tag, "")
        if desc:
            lines.append(f"- {tag} — {desc} ({count})")
        else:
            lines.append(f"- {tag} ({count})")

    return "\n".join(lines)


def update_claude_md(claude_md: Path, new_registry: str) -> str:
    """Update the Tag Registry section in CLAUDE.md. Returns new content."""
    try:
        content = claude_md.read_text(encoding="utf-8")
    except OSError:
        return ""

    # Find the Tag Registry section
    lines = content.splitlines()
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        if re.match(r"^##\s+Tag\s*Registry", line, re.IGNORECASE):
            start_idx = i
            continue
        if start_idx is not None and line.startswith("## ") and i > start_idx:
            end_idx = i
            break

    if start_idx is None:
        # Append at end
        return content + "\n## Tag Registry\n\n" + new_registry + "\n"

    if end_idx is None:
        end_idx = len(lines)

    # Replace the section content (keep the heading)
    new_lines = lines[: start_idx + 1] + [""] + new_registry.splitlines() + [""] + lines[end_idx:]
    return "\n".join(new_lines)


def main() -> None:
    write_mode = "--write" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        knowledge_dir = Path(args[0]).resolve()
    else:
        knowledge_dir = Path(".claude/knowledge").resolve()

    entries_dir = knowledge_dir / "entries"
    claude_md = knowledge_dir / "CLAUDE.md"

    if not entries_dir.is_dir():
        print(f"Error: {entries_dir} is not a directory")
        sys.exit(1)

    print(f"Knowledge directory: {knowledge_dir}")
    print(f"Mode: {'WRITE' if write_mode else 'DRY RUN'}")
    print()

    # Scan entries
    tag_counts = scan_entries(entries_dir)
    print(f"Found {len(tag_counts)} unique tags across entries")
    print()

    # Parse existing descriptions
    descriptions = parse_existing_registry(claude_md)
    preserved = sum(1 for t in tag_counts if t in descriptions and descriptions[t])
    print(f"Preserved {preserved} existing descriptions")

    # New tags (in entries but not in registry)
    new_tags = [t for t in tag_counts if t not in descriptions]
    if new_tags:
        print(f"New tags: {', '.join(sorted(new_tags))}")

    # Unused tags (in registry but not in entries)
    unused = [t for t in descriptions if t not in tag_counts]
    if unused:
        print(f"Unused tags (will be removed): {', '.join(sorted(unused))}")

    print()

    # Format registry
    new_registry = format_registry(tag_counts, descriptions)
    print("--- Generated Registry ---")
    print(new_registry)
    print("---")

    if write_mode:
        if claude_md.is_file():
            new_content = update_claude_md(claude_md, new_registry)
            claude_md.write_text(new_content, encoding="utf-8")
            print(f"\nUpdated {claude_md}")
        else:
            print(f"\nError: {claude_md} not found")
            sys.exit(1)
    else:
        print("\nRe-run with --write to apply changes.")


if __name__ == "__main__":
    main()
