# Upgrading

> 日本語版: [upgrading.ja.md](upgrading.ja.md)

You have updated ccmemo, or are about to. This page tells you whether your
knowledge base needs any work afterwards, and how to do that work if you
choose to. Most updates need nothing from you.

## Terms used on this page

| Term | Meaning |
|---|---|
| knowledge base | The `.claude/knowledge/` folder of a project. |
| entry | One Markdown file in the knowledge base. |
| frontmatter | The block between the `---` lines at the top of an entry. |
| lint | The check you run with `kb_graph.py lint`. Some people also run it from a pre-commit hook. |

## Do I need to do anything?

| You are updating from | What to read |
|---|---|
| 1.27.0 or later | Nothing to do. |
| 1.26.x | [1.27: entry ids and verification](#127-entry-ids-and-verification). One command plus one line; nothing breaks if you skip it. |
| 1.25.x or 1.24.x | The section above, plus [1.26: descriptions and link labels](#126-descriptions-and-link-labels). Optional work; nothing breaks if you skip it. |
| 1.23.x or older | Both sections above, plus [1.24: tags and status](#124-tags-and-status). No work needed there, but two behaviours change. |

## What happens on its own

**The plugin itself.**  
`/plugin update ccmemo` fetches the new version and `/reload-plugins`
activates it in the running session. That is the whole update.

**The search index.**  
ccmemo keeps a search index in `.claude/knowledge/.index/`. When a new
version changes its layout, the next search rebuilds what it needs from your
Markdown files. You never rebuild it by hand.

**New rules for entries are opt-in.**  
The lint checks entries against a set of rules, and new versions sometimes
add rules. A new rule does not fail your lint until you say your knowledge
base follows it. You say so by writing a rule version at the top of
`.claude/knowledge/CLAUDE.md`:

```yaml
---
schema_version: 2
---
```

Until you do, the lint still lists what the new rules would report, but
under a heading marked *advisory*, and the exit code stays 0. A pre-commit
hook therefore keeps passing after an update. When you want to try the new
rules before committing to them, run `kb_graph.py --schema 2 lint`.

## 1.27: entry ids and verification

### What changed

Every entry now carries an **id**, a random identifier written once in its
frontmatter (`id:`). The filename still says when the entry was created and
what it is about; the id says *which* entry it is, so renaming a file or
copying it into another knowledge base no longer loses track of it.
`kb_graph.py rename` changes a slug and rewrites every link to it;
`kb_graph.py relink` repairs links after a file was moved by hand.

Every entry also records **who wrote it and who checked it**, separately.
`generated:` names the writer (a person, Claude, or a process) and the time;
`verified:` is a list of independent checks, added with
`kb_graph.py verify`, never by hand. Search shows a checked entry with
`[human]` or `[machine]` after its title and can filter on it
(`--verified human`). The old `confidence:` field is retired: the writer's
own rating never counted as a check. You do not need to delete it.

These are the rules of **schema version 3**. Until you declare
`schema_version: 3`, the two new lint checks (`missing-id`,
`missing-generated`) are advisory. Entries created with `/record-knowledge`
carry both fields from the start.

### What you will notice after the update

- `kb_graph.py lint` lists every entry without an id or a `generated:` block
  under the advisory heading. The exit code does not change.
- After Claude edits an old entry, a one-line notice says those checks are
  not enforced yet.
- Nothing in search output changes until an entry has been verified.

### If you want to adopt the new rules

1. Add the two fields to every entry. One command does it, and running it
   again changes nothing:

   ```bash
   python3 scripts/kb_graph.py --root .claude/knowledge/entries migrate --to 3 --tz +09:00
   ```

   `--tz` is the time zone your entries were written in; it is only used to
   turn each filename's date-time into `generated.at`. Every entry gets an
   `id` and a `generated` block naming `claude-code` as the writer (pass
   `--by human:<handle>` if you wrote them yourself). Nothing else in the
   file is touched, and no entry is marked verified: nobody has checked the
   old ones yet, and the point of the field is to say so honestly.

   ```yaml
   # Before
   title: Plugin cache is keyed by version
   author: "@alice"
   created: 2026-08-11

   # After
   title: Plugin cache is keyed by version
   id: 1887fedb-0afd-479e-98ef-e02b80b1fb00
   author: "@alice"
   created: 2026-08-11
   generated:
     by: claude-code
     at: 2026-08-11T17:15:10+09:00
   ```

2. Write `schema_version: 3` at the top of `.claude/knowledge/CLAUDE.md`
   (replace the `2`). From now on the id and writer fields are required,
   including right after Claude saves an entry.

3. Run the lint and confirm it reports 0 findings:

   ```bash
   python3 scripts/kb_graph.py --root .claude/knowledge/entries lint
   ```

4. Commit the changed entries.

Two things to know afterwards. Because every entry's text changed, the next
search re-embeds the whole knowledge base once; on a few hundred entries that
takes from under a minute to a few minutes depending on the machine, so you
may prefer to run
`uv run scripts/kb_index.py .claude/knowledge/entries` yourself first. And
when you have actually checked an entry — read it and confirmed it still
holds — record that with
`python3 scripts/kb_graph.py --root .claude/knowledge/entries verify <entry> --by human:<handle>`;
`/review-knowledge` does the same for entries confirmed during a review.
A passing lint is not a check and is never recorded as one.

## 1.26: descriptions and link labels

### What changed

An entry can now say when it should be opened. That sentence lives in the
frontmatter as `description:`. Search results and the automatic suggestions
at the start of a prompt show it, so you, or Claude, can pick the right
entry without opening several. Entries without a description keep working;
the first paragraph of the body is shown instead, marked `(lead)`.

Links between entries (`- see:`, `- ref:`, `- amends:`, `- extends:`) are
expected to end with a short reason, after the link, on the same line:

```markdown
- see: [Plugin cache is keyed by version](2026/08/20260811-171510-alice-plugin-cache.md) — why a merged fix did not reach users
```

Two new rules go with this: every entry has a description of 80 to 320
characters, and every link has a reason. Both belong to rule version 2, so
they are advisory until you declare `schema_version: 2`. Entries you create
with `/record-knowledge` already follow both.

### What you will notice after the update

- `kb_graph.py lint` prints an *advisory* block listing entries without a
  description and links without a reason. The exit code does not change.
- When Claude edits an old entry, a one-line notice says that the checks are
  not enforced yet.

### If you want to adopt the new rules

1. See how much work there is:

   ```bash
   python3 scripts/kb_graph.py --root .claude/knowledge/entries --schema 2 lint
   ```

   Each entry that would fail is listed with the reason.

2. Give each entry a description. Write the situations in which someone
   should open it: the symptom they see, the question they have, or the
   decision they are about to make. Put the most common situation first,
   since search results show only the first 80 characters. Do not restate
   the title; the title already gives the answer.

   ```yaml
   # Before
   title: Plugin cache is keyed by version

   # After
   title: Plugin cache is keyed by version
   description: "Open when a plugin fix merged to main does not reach users, when /plugin update says already at the latest version yet the old behaviour persists, or when deciding whether a fix PR must carry the version bump."
   ```

   If you have many entries, ask Claude to do this in batches of 20 to 30.
   Put the rules above and a few good examples in one file and hand it to
   each batch; afterwards run the command from step 1 to check.

3. Add a reason to every link that lacks one. Keep it on the same line as
   the link; the lint reads one line at a time.

4. When step 1 reports nothing, write `schema_version: 2` at the top of
   `.claude/knowledge/CLAUDE.md`. From now on the two rules are enforced,
   including right after Claude saves an entry.

One caution for batch edits: if the redact hook is enabled, every edited
entry passes through it. That can be useful, since it also catches secrets
that were already in old entries, but it sometimes masks harmless text that
looks like an e-mail address (a systemd unit name such as
`app-foo@autostart.service`, or an SSH URL). If that happens, restore the
text with a shell command rather than with the Edit tool, because the Edit
tool triggers the hook again.

## 1.24: tags and status

All parts of ccmemo now read the frontmatter with the same parser. Nothing
needs rewriting, but two things behave differently:

- `tags:` written as a list, one `"#tag"` per line, is now understood by the
  search scripts. Before 1.24 they silently treated such a list as no tags.
  The list is the recommended form for new entries; the one-line form
  `tags: "#a #b"` keeps working, so leave old entries as they are.
- An entry with no `status:` line now counts as `active` everywhere. Before
  1.24 the search scripts left such entries out of `--status active`.
- Tags that start with a digit, such as `#1password`, are now recognised.
