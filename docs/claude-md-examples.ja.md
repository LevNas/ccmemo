# CLAUDE.md 設定例

> English version: [claude-md-examples.md](claude-md-examples.md)

ccmemo を Claude Code のワークフローへ組み込むための、プロジェクト `CLAUDE.md` の設定例集です。
必要な節だけを選び、プロジェクトに合わせて調整してください。

貼り付け用のスニペットは英語のまま掲載しています。
CLAUDE.md は Claude への指示文であり、英語で書くと語彙がコードやツールの識別子とそのまま一致し、チームや他プロジェクトへの可搬性も保てるためです（[言語](#言語)の節を参照）。
日本語で書いても動作するので、人間のメンバーが頻繁に読み書きする規則を日本語にする選択も成り立ちます。

## 思考パートナー宣言

Claude Code をコーディング支援に限らず「考える相棒」として使うことを明示します。
ナレッジの記録や参照は、コードを書かない検討セッションでも発生するためです。

```markdown
# Project Harness

Claude Code is not just a coding assistant — it also serves as a "thinking partner."
```

## 何を最適化するか

プロジェクトが優先する価値を短く宣言しておくと、提案や実装の方向がぶれにくくなります。

```markdown
## What to optimize for
- Reproducibility over cleverness
- Small diffs, fast feedback loops
- Simplicity — but never at the cost of usability
```

## 応答品質

あいまいな入力の構造化と、記録済みナレッジを根拠にした応答を促します。
中核は **Knowledge-first flow**（どの応答でも、外部ソースにあたる前にまずナレッジを検索する手順）です。

```markdown
## Response Quality
- Perform a 3-level self-review before responding (broad → mid → narrow perspective)
- If any command or code hasn't been verified, disclose that before presenting it
- **Structuring**: When the user's message is ambiguous, restate it as a structured summary
  (bullet points, indentation, markdown) at the top of the response before proceeding
- **Knowledge-first flow** (in order, for EVERY response):
  1. Search `.claude/knowledge/entries/` for relevant active entries BEFORE any other action
  2. If found, use the knowledge as the basis for the response
  3. If not found, proceed to external sources (APIs, file reads, etc.)
  4. If still uncertain, offer a clarifying question to the user rather than guessing
  5. If a question doesn't apply, respond with "No knowledge entry found —
     the following includes inference/speculation"
```

## ナレッジ記録ルール

エントリの置き場所と、「1エントリ1ファイル、既存エントリは直接編集し、履歴は git に任せる」という運用を宣言します。

```markdown
## Knowledge Recording
- Aggregate knowledge in `.claude/knowledge/entries/` as one file per entry (with YAML frontmatter)
- Entries are mutable — edit in place, rely on git for history
- See `/record-knowledge` skill for format and consolidation procedures
```

## ナレッジベース参照

応答前のナレッジ検索を「最初の行動」として固定する節です。
ナレッジエントリは最速の一次情報なので、先に確認すると不要な API 呼び出しやファイル読みを省けます。
想定ヒット件数に応じて検索手段を使い分けます。
複数件が見込まれるなら Explore サブエージェントに委譲してメイン文脈を汚さず、1件だけなら直接 `rg` で読み、キーワードが空振りしたら `/recall-knowledge` の意味検索で拾い直します。

```markdown
## Knowledge Base Lookup

**Before responding to any user message**, search for relevant active knowledge entries.
This is the FIRST action — do it before calling external APIs, reading files, or reasoning about the answer.
Use the Explore subagent for searches that may hit multiple entries (keeps main context clean).

### When to search
- **Always**: when the user asks about schedules, status, decisions, or project context
- **Always**: at session start, before beginning any work
- **Recommended**: when the topic involves a specific service, server, or workflow

### Search (multiple entries expected)

Delegate to Explore subagent:
- "Search `.claude/knowledge/entries/` for active entries related to `<keyword>` and summarize relevant findings"
- The subagent reads entries and returns only a concise summary to the main context

### Search (single entry expected)

Read directly — subagent overhead is unnecessary:
\```bash
rg '^status: active' .claude/knowledge/entries/ -l | xargs rg '<keyword>' -l
\```
Then Read the matching file.

### Search by meaning (keyword search came up empty)

When literal keywords miss — synonyms, or a query worded differently from the entries
(e.g. a Japanese query against English identifiers) — run the `/recall-knowledge` skill.
It does hybrid semantic search (ripgrep + local vector embeddings + the `see:`-link graph)
and falls back to ripgrep when the index is unavailable, so it is always safe to try.
For multi-hop questions (how a decision evolved, how two topics connect) it queries the
link-graph structure first and reads only the endpoint entries (see docs/link-graph.md).

### Rules
- Only reference entries with `status: active` — ignore `deprecated` entries
- Replace `<keyword>` with terms relevant to the current task (service name, technology, etc.)
- If keyword search returns nothing relevant, retry with `/recall-knowledge` before
  concluding "no knowledge entry found"
```

## プラン永続化（Git-tracked モード）

`.claude/tasks/` を Git にコミットするプロジェクト向けです。
セッション開始時に未完了プランを確認する習慣を固定します。

```markdown
## Plan Persistence
- Save plans and work status as files in `.claude/tasks/` (see `.claude/tasks/CLAUDE.md` for details)
- At session start, check `.claude/tasks/readme.md` for incomplete plans before starting work
```

## プラン永続化（Issue-centric モード）

イシュートラッカーを一次情報にするプロジェクト向けです。
`.claude/tasks/` はローカルの下書き置き場（gitignore 対象）と位置づけ、セッション開始時の確認先を割り当てイシューに変えます。

```markdown
## Plan Persistence
- **Primary**: Issue tracker is the single source of truth for plans and progress
  - Create plan issues using a plan template
  - Track progress via checklist updates + comments
- **Secondary**: `.claude/tasks/` is a local working memo (gitignored)
  - Persists across Claude Code sessions on the same machine
  - Not shared with other members — anything worth sharing belongs in the issue tracker
- **Session Start**:
  1. Check assigned issues in your tracker (e.g., `glab issue list --assignee=@me`)
  2. Read the target issue's comments to understand the latest state
```

## イシュー管理

Issue-centric モードのチーム向けに、イシューの品質チェックと停滞診断を習慣化します。

```markdown
## Issue Management
- Issue quality checks — run when creating, reading, or updating issues:
  - Granularity: is the scope small enough for one person to complete in a reasonable timeframe?
  - Priority: does it have a priority label? Is the priority justified?
  - Risk: are risks and blockers identified?
- Stalled issue diagnosis: scan on-hold issues for stalled patterns at session start
- Details can be separated into knowledge entries to keep CLAUDE.md concise
```

## 進捗更新

「進捗更新して」という一言をトリガーフレーズにして、複数の更新作業を一括実行させます。

### Git-tracked モード

```markdown
## Progress Update
When the user says "update progress", execute all of the following:
1. Update `.claude/tasks/` todo.md and readme.md
2. Record any knowledge gained during work
3. Commit and push changes
```

### Issue-centric モード

```markdown
## Progress Update
When the user says "update progress", execute all of the following:
1. Post a progress comment on the issue (latest status, completed items, next actions)
2. Update issue labels, milestones, and checklists
3. Update related document execution records and changelogs
4. Commit and push changes
5. Record any knowledge gained during work to `.claude/knowledge/entries/`
```

## エージェントの使い方

主目的でない作業をサブエージェントへ委譲し、メインセッションの文脈を軽く保ちます。
生の検索結果ではなく要約だけをメイン文脈へ戻すのが要点です。

```markdown
## Agent Behavior
- Delegate non-primary work to subagents to keep the main session context lean
  - Research, multi-file searches, knowledge lookups → Explore subagent
  - Only pull the summary back into the main context, not raw results
```

## 言語

設定やデータに使う言語と、ユーザーとの対話に使う言語を分離します。

```markdown
## Language
- Claude Code config/data (CLAUDE.md, `.claude/tasks/`): any language (English recommended)
- Respond to the user in their preferred language
- Knowledge entries: user's preferred language
- Issue tracker: team's common language
```
