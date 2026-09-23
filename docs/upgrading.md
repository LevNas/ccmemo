# Upgrading

> 日本語版: [upgrading.ja.md](upgrading.ja.md)

You have updated ccmemo, or are about to. This page tells you whether your
knowledge base needs any work afterwards, and how to do that work if you
choose to. Most updates need nothing from you.

A few words used below:  
your **knowledge base** is the `.claude/knowledge/` folder of a project.  
An **entry** is one Markdown file in it.  
The **frontmatter** is the block between the `---` lines at the top of an entry.  
The **lint** is the check you run with `kb_graph.py lint` (some people also run it from a pre-commit hook).

## Do I need to do anything?

| You are updating from | What to read |
|---|---|
| 1.26.0 or later | Nothing to do. |
| 1.25.x or 1.24.x | [1.26: descriptions and link labels](#126-descriptions-and-link-labels). Optional work; nothing breaks if you skip it. |
| 1.23.x or older | The section above, plus [1.24: tags and status](#124-tags-and-status). No work needed there, but two behaviours change. |

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
