# Getting Started

> 日本語版: [getting-started.ja.md](getting-started.ja.md)

A zero-to-first-recall walkthrough: lay out your repositories, install ccmemo
once at user scope, scaffold one repository, and run the record → commit →
recall loop. The condensed version of this page is the README
[Quick start](../README.md#quick-start); day-to-day workflows live in
[usage.md](usage.md).

## What you end up with

- ccmemo installed **once**, active in every repository you open
- a predictable clone layout where each repository carries its own knowledge
  base under `.claude/knowledge/`
- your first entry recorded, committed, and recalled by a fresh session

## Prerequisites

Nothing beyond Claude Code and `git` is *required* to start: every feature
layer checks for its tools and **silently stays dormant** when they are
missing. Install only the layers you want.

| Layer | Powers | Tools | When absent |
|---|---|---|---|
| Core skills | `/record-knowledge`, `/plan-task`, `/review-knowledge` | [Claude Code](https://code.claude.com/docs/en/overview), `git` | — |
| Safety hooks & graph CLI | secret redaction, context guard, opt-in auto-commit, tasks mirroring, `kb_graph.py` (lint / `link-add` / `supersede` / `lineage`) | `python3` (3.10+, stdlib only — no pip packages) | hooks skip silently; graph CLI unavailable |
| Per-prompt auto-search | 関連ナレッジ候補 injected on every prompt | `bash`, `jq`, [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`), [`mecab`](https://taku910.github.io/mecab/) + a dictionary | hook silently does nothing; `rg` also backs `/recall-knowledge`'s index-free fallback |
| Semantic search (Step 5) | vector recall in `/recall-knowledge` | [`uv`](https://docs.astral.sh/uv/) (simplest) or `python3` + pip (`fastembed`, `sqlite-vec`); ~220 MB one-time local model download | falls back to ripgrep-only recall |

Optional either way: [ghq](https://github.com/x-motemen/ghq) for the clone
layout below.

### Installing the tools

Ubuntu / Debian (including WSL):

```bash
sudo apt install python3 jq ripgrep mecab mecab-ipadic-utf8
curl -LsSf https://astral.sh/uv/install.sh | sh   # only for Step 5
```

macOS (Homebrew):

```bash
brew install python jq ripgrep mecab mecab-ipadic uv
```

The auto-search hook tokenizes prompts with mecab, so it needs a dictionary
package too (`mecab-ipadic-utf8` on apt, `mecab-ipadic` on brew) — mecab
alone on `PATH` is not enough.

### Platform support

- **Linux** (including WSL2) — the development and daily-driver platform;
  everything above is verified here. NixOS has one known quirk in the
  semantic-search layer (`libstdc++` resolution for numpy), handled
  automatically — see [hybrid-search.md](hybrid-search.md).
- **macOS** — expected to work (every dependency is a Homebrew formula and
  the hooks are plain bash/python3), but not regularly tested. Reports
  welcome.
- **Windows (native)** — partial. Core skills work. The python hooks run
  only if `python3` (not just `python`) resolves on `PATH`, and the
  auto-search hook is a bash script, so it needs Git Bash; mecab is
  impractical to install natively, which makes the auto-search layer
  effectively Linux/macOS/WSL-only. On Windows, WSL is the recommended way
  to run the full stack.

## Step 1 — Lay out your repositories

ccmemo keeps knowledge *inside* each repository (`.claude/knowledge/`), so it
follows the code: branches, reviews, clones, and teammates all carry the
knowledge base with them. Once several repositories have one, a predictable
clone layout is what keeps them all findable.

We recommend the `~/src/<host>/<owner>/<repo>` layout:

```
~/src/
└── github.com/
    └── you/
        ├── app     ← knowledge base at ~/src/github.com/you/app/.claude/knowledge/
        └── infra   ← knowledge base at ~/src/github.com/you/infra/.claude/knowledge/
```

Plain `git clone` works:

```bash
git clone https://github.com/you/app ~/src/github.com/you/app
```

[ghq](https://github.com/x-motemen/ghq) automates exactly this layout, and is
worth adopting once you manage more than a handful of clones:

```bash
git config --global ghq.root '~/src'
ghq get github.com/you/app        # clones to ~/src/github.com/you/app
ghq list                          # every repository, one line each
```

ghq is optional — nothing in ccmemo depends on it. But `ghq list` piped into
your fuzzy finder plus a per-repository knowledge base means: open any repo,
and its knowledge is already there.

## Step 2 — Install at user scope

In any Claude Code session:

```
/plugin marketplace add LevNas/claudecode-plugins
/plugin install ccmemo@levnas-plugins
```

When Claude Code asks for a scope, choose **User** — the plugin installs once
under `~/.claude/` and is active in *every* repository you open, which is what
this guide assumes. (Project and local scopes exist for narrower setups.) The
same install from a shell, non-interactively:

```bash
claude plugin install ccmemo@levnas-plugins --scope user
```

If the install summary says `Run /reload-plugins to activate`, do that (or
start a new session). Then verify:

- `/plugin list` shows ccmemo as installed
- typing `/ccmemo:` autocompletes the four skills
  (`record-knowledge`, `recall-knowledge`, `review-knowledge`, `plan-task`)

The skills and the Context Guard hooks are active from here — there is no
per-repository install step.

**Team note** — to auto-enable ccmemo for everyone who opens a shared project,
commit this to the project's `.claude/settings.json` (teammates still run the
`marketplace add` line once):

```json
{
  "enabledPlugins": {
    "ccmemo@levnas-plugins": true
  }
}
```

## Step 3 — Scaffold a repository

Once per repository, scaffold the starter config — a tag registry for
knowledge and an index for tasks. In a session opened at the repository root,
just ask:

> Scaffold ccmemo's knowledge and tasks templates into `.claude/`.

which creates:

```
.claude/
├── knowledge/   (CLAUDE.md tag registry + entries/)
└── tasks/       (CLAUDE.md + readme.md index)
```

Prefer a shell command over asking? See
[usage.md — Scaffolding templates by hand](usage.md#scaffolding-templates-by-hand-plugin-install).

## Step 4 — Record, commit, recall

1. **Record.** Run `/record-knowledge` and describe something worth keeping —
   a pitfall you just hit, a decision and its rationale, an undocumented
   quirk. Claude writes a tagged Markdown entry under
   `.claude/knowledge/entries/`.
2. **Commit.** The entry is a normal file — commit it like code. On a team it
   rides the same branch/review flow as everything else.
3. **Recall.** Start a fresh session and ask about the topic, or run
   `/recall-knowledge` explicitly. The entry that would otherwise have lived
   and died in one session is now part of the repository.

That loop — record, commit, recall — is the whole core of ccmemo. Everything
else is leverage on top of it.

## Step 5 (optional) — Enable semantic search

`/recall-knowledge` works without any setup, in ripgrep-only mode. To add
meaning-based retrieval — synonyms, or a Japanese query against English
identifiers — build the local vector index once per machine:

```bash
# Keep the derived index out of git (once per repository)
echo '.claude/knowledge/.index/' >> .gitignore

# Build the index. uv installs the dependencies (fastembed + sqlite-vec)
# into an ephemeral env, and the embedding model downloads on first run.
# Everything runs locally — knowledge text never leaves the machine.
scripts=$(find ~/.claude/plugins/cache -type d -path '*ccmemo*/scripts' | sort | tail -1)
uv run "$scripts/kb_index.py" .claude/knowledge/entries/
```

Verify with a query:

```bash
uv run "$scripts/kb_search.py" .claude/knowledge/entries/ "your query"
```

From here `/recall-knowledge` uses the index automatically. The index is a
per-machine derived cache: re-runs are incremental, searches refresh changed
entries lazily, and it can be regenerated at any time. Prefer plain Python?
`pip install 'fastembed>=0.3' 'sqlite-vec>=0.1.6'` + `python3` works too —
that path, the post-merge re-index hook for teams, and the NixOS note are in
[hybrid-search.md](hybrid-search.md).

## Going further

- [usage.md](usage.md) — searching entries, reviewing the base, plans & tasks,
  customization
- [hybrid-search.md](hybrid-search.md) — semantic search in depth: the search
  pipeline and filters, the pip path, post-merge re-indexing, NixOS
- [link-graph.md](link-graph.md) — structural queries over the `see:`-link
  graph: hubs, orphans, shortest paths
- [claude-md-examples.md](claude-md-examples.md) — wiring ccmemo conventions
  into your project's CLAUDE.md

## Scale it with ccorch

ccmemo alone covers the single-session loop. Its sibling plugin
[ccorch](https://github.com/LevNas/ccorch) — same marketplace — adds subagent
orchestration, and two of its nine bundled agent types are built specifically
around a ccmemo-shaped knowledge base:

- **`ccorch:kb-integrator`** reads ten or more entries and returns a cited
  synthesis. This is what keeps a *growing* knowledge base usable: "what do we
  already know about X?" stays a one-question sweep even after the entries
  outgrow what you would re-read by hand.
- **`ccorch:knowledge-recorder`** drafts entries following your
  `/record-knowledge` conventions. A large orchestrated work session can end
  with its discoveries drafted as entries in parallel — while the decision of
  *what* is worth recording stays with you.

Together the two plugins close a loop neither has alone: orchestrate work at
scale → capture what the waves discovered → recall it before deciding anything
twice. ccmemo's `/plan-task` spans the sessions in between.

```
/plugin install ccorch@levnas-plugins
```

Setup walkthrough on the ccorch side:
[ccorch docs/getting-started.md](https://github.com/LevNas/ccorch/blob/main/docs/getting-started.md).
