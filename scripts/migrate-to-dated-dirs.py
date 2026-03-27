#!/usr/bin/env python3
"""Migrate flat .claude/knowledge/entries/ to YYYY/MM/ subdirectory structure.

Moves entries with YYYYMMDD-* filenames into entries/YYYY/MM/ subdirectories
and updates all see links to use entries/-relative paths (e.g., 2026/03/slug.md).

Usage:
    python3 migrate-to-dated-dirs.py [entries_dir]
    python3 migrate-to-dated-dirs.py              # defaults to .claude/knowledge/entries/
    python3 migrate-to-dated-dirs.py --dry-run     # preview without moving
"""

import os
import re
import sys
from pathlib import Path


def parse_date_prefix(filename: str) -> tuple[str, str] | None:
    """Extract (YYYY, MM) from a YYYYMMDD-* filename."""
    match = re.match(r"^(\d{4})(\d{2})\d{2}-", filename)
    if match:
        return match.group(1), match.group(2)
    return None


def build_move_plan(entries_dir: Path) -> list[tuple[Path, Path]]:
    """Build a list of (source, destination) pairs for migration."""
    plan = []
    for f in sorted(entries_dir.iterdir()):
        if not f.is_file() or not f.suffix == ".md":
            continue
        if f.name == "CLAUDE.md":
            continue
        date = parse_date_prefix(f.name)
        if not date:
            # Legacy entries without date prefix — skip
            continue
        year, month = date
        dest_dir = entries_dir / year / month
        dest = dest_dir / f.name
        if f != dest:
            plan.append((f, dest))
    return plan


def build_link_map(plan: list[tuple[Path, Path]], entries_dir: Path) -> dict[str, str]:
    """Build a mapping from old see-link references to new ones.

    Maps: 'slug.md' -> 'YYYY/MM/slug.md'
    """
    link_map = {}
    for _src, dest in plan:
        filename = dest.name
        rel = dest.relative_to(entries_dir)
        link_map[filename] = str(rel)
    # Also include files already in subdirectories
    for f in entries_dir.rglob("*.md"):
        if f.name == "CLAUDE.md":
            continue
        if f.parent == entries_dir:
            continue  # flat files handled above
        rel = f.relative_to(entries_dir)
        link_map[f.name] = str(rel)
    return link_map


def update_see_links(content: str, link_map: dict[str, str]) -> str:
    """Update see/ref links that reference entries by filename only."""
    def replace_link(m: re.Match) -> str:
        prefix = m.group(1)   # "- see: [title](" or similar
        target = m.group(2)   # "slug.md" or "2026/03/slug.md"
        suffix = m.group(3)   # ")" or ") — description"

        # Extract just the filename from the target
        target_filename = os.path.basename(target)

        if target_filename in link_map:
            new_target = link_map[target_filename]
            return f"{prefix}{new_target}{suffix}"
        return m.group(0)

    # Match markdown links to .md files in see/ref lines
    # Pattern: [text](path.md) with optional surrounding content
    pattern = re.compile(
        r"(\[[^\]]*\]\()([^)]*\.md)(\))"
    )
    return pattern.sub(replace_link, content)


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        entries_dir = Path(args[0]).resolve()
    else:
        entries_dir = Path(".claude/knowledge/entries").resolve()

    if not entries_dir.is_dir():
        print(f"Error: {entries_dir} is not a directory")
        sys.exit(1)

    print(f"Entries directory: {entries_dir}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    # Step 1: Build move plan
    plan = build_move_plan(entries_dir)
    if not plan:
        print("No entries to migrate.")
        return

    print(f"Files to move: {len(plan)}")
    for src, dest in plan:
        print(f"  {src.name} -> {dest.relative_to(entries_dir)}")
    print()

    # Step 2: Build link map (includes both planned moves and existing)
    link_map = build_link_map(plan, entries_dir)

    # Step 3: Move files
    if not dry_run:
        for src, dest in plan:
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
        print(f"Moved {len(plan)} files.")
    else:
        print(f"Would move {len(plan)} files.")

    # Step 4: Update see links in all entries
    updated_count = 0
    all_entries = list(entries_dir.rglob("*.md"))
    for entry in all_entries:
        if entry.name == "CLAUDE.md":
            continue
        try:
            content = entry.read_text(encoding="utf-8")
        except OSError:
            continue

        new_content = update_see_links(content, link_map)
        if new_content != content:
            updated_count += 1
            if not dry_run:
                entry.write_text(new_content, encoding="utf-8")
            else:
                print(f"  Would update links in: {entry.relative_to(entries_dir)}")

    # Step 5: Update CLAUDE.md in knowledge/ root (if exists)
    knowledge_dir = entries_dir.parent
    claude_md = knowledge_dir / "CLAUDE.md"
    if claude_md.is_file():
        content = claude_md.read_text(encoding="utf-8")
        new_content = update_see_links(content, link_map)
        if new_content != content:
            updated_count += 1
            if not dry_run:
                claude_md.write_text(new_content, encoding="utf-8")
            else:
                print(f"  Would update links in: CLAUDE.md")

    print(f"Updated see links in {updated_count} files.")

    # Step 6: Summary
    print()
    subdirs = set()
    for _src, dest in plan:
        subdirs.add(str(dest.parent.relative_to(entries_dir)))
    print(f"Created subdirectories: {', '.join(sorted(subdirs))}")
    print()
    if dry_run:
        print("Re-run without --dry-run to apply changes.")
    else:
        print("Migration complete. Review with 'git diff' and commit.")


if __name__ == "__main__":
    main()
