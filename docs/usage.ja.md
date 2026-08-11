# 使い方ガイド

> English version: [usage.md](usage.md)

ccmemo をインストールしたあとの日常のワークフローと、手動インストール、カスタマイズをまとめます。
最短のインストール手順は README の [Quick start](../README.md#quick-start) に、日本語での導入の全体像は [導入ガイド](getting-started.ja.md) にあります。

## マーケットプレイスを使わないインストール

スキルとテンプレートをリポジトリから直接コピーします。

```bash
git clone --depth 1 https://github.com/LevNas/ccmemo /tmp/ccmemo
cp -r /tmp/ccmemo/skills/*            .claude/skills/      # 4つのスキル
cp -r /tmp/ccmemo/templates/knowledge .claude/knowledge
cp -r /tmp/ccmemo/templates/tasks     .claude/tasks
rm -rf /tmp/ccmemo
```

プロジェクトは次の構成になります。

```
your-project/.claude/
├── skills/{record-knowledge,plan-task,review-knowledge,recall-knowledge}/SKILL.md
├── knowledge/{CLAUDE.md,entries/}
└── tasks/{CLAUDE.md,readme.md}
```

フック（Context Guard）はプラグイン経由で配線されるため、手動コピーの場合に使いたければ `hooks/hooks.json` のエントリを自分で追加します。

### シェルでの scaffold（プラグインインストール時）

マーケットプレイス経由でインストール済みで、Claude Code に頼む代わりにシェルコマンドで scaffold したい場合は、プロジェクトルートで実行します。

```bash
tpl=$(find ~/.claude/plugins/cache -type d -path '*ccmemo*/templates' | sort | tail -1)
cp -r "$tpl/knowledge" .claude/knowledge
cp -r "$tpl/tasks"     .claude/tasks
```

## エントリを検索する

キーワードやファイル名での検索は、準備なしで使えます。

```bash
fd -e md . .claude/knowledge/entries/ | fzf   # ファイル名のあいまい検索
rg '#pitfall' .claude/knowledge/entries/      # タグで検索
rg '^title:' .claude/knowledge/entries/       # 全タイトルを列挙
```

意味での検索（同義語や、英語の識別子に対する日本語クエリ）には `/recall-knowledge` を使います。
ripgrep、ローカルのベクトル埋め込み、`see:` リンクグラフを組み合わせたハイブリッド検索で、索引が無いときは ripgrep のみの検索に自動で切り替わります。
セットアップは [hybrid-search.md](hybrid-search.md)（英語）を参照してください。

「この決定はどう変遷したか」「X と Y はどうつながるか」のような複数ホップの質問には、`scripts/kb_graph.py` が `see:` リンクグラフを直接照会します。
Python 標準ライブラリだけで動き、索引も要りません。
サブコマンドと pre-commit lint は [link-graph.md](link-graph.md)（英語）にあります。

## ナレッジをレビューする

`/review-knowledge` は3つのモードでナレッジベースの健全性を保ちます。

- **ヘルスチェック**（既定）：古いエントリ（90日超）、孤立エントリ（`see:` リンクなし）、リンクの張り忘れ、タグの問題、集計情報を報告します
- **トピックレビュー**（`topic:<キーワード>`）：トピックを要約し、内容が今も正確かを確かめる問いを投げます
- **修正モード**（`fix`）：対話的に、欠けているリンクの追加とタグの登録を行います

## プランとタスクの管理

`/plan-task` は複数ステップのプランを `.claude/tasks/` に永続化します。
モードは2つあります。

- **Git-tracked**（既定）：プラン（`plan-v1.md`、`todo.md`、`readme.md`）をコミットし、共有の一次情報にします。進捗は `[ ]` → `[~]` → `[x]` と進め、プランの改訂は `plan-v2.md` のように版を重ねます。各タスクディレクトリには、作業文脈を詳細に残す `context-*.md`（[Context Guard](architecture.md#context-guard-since-v110) 参照）も置かれます。
- **Issue-centric**：`.claude/tasks/` を gitignore し、イシュートラッカー（GitHub、GitLab、Jira）を一次情報として扱います。`.claude/tasks/` はセッションごとの下書き置き場になります。セッション開始時には `readme.md` ではなく、自分に割り当てられたイシューを確認します。

## CLAUDE.md への組み込み

プロジェクトの `CLAUDE.md` に参照節を足しておくと、Claude は作業前に関連エントリを確認するようになります。
パターンと実例は [claude-md-examples.ja.md](claude-md-examples.ja.md) にあります。

## カスタマイズ

- **タグ**：`.claude/knowledge/CLAUDE.md` に台帳を維持します（小文字の kebab-case、`#` 接頭辞）。Claude は新しいタグを作る前にこの台帳を確認します。
- **著者**：エントリの `author` フィールドの既定は `@<ユーザー名>` です。Git ホスティングのユーザー名に設定します。
- **ワークフロー**：`skills/*/SKILL.md` を編集すると、プロジェクト固有の規約（タグの分類、イシューコメントの書式、プランのテンプレート）を足せます。
- **自動コミット（オプトイン、既定はオフ）**：`CCMEMO_AUTOCOMMIT=1` を設定すると、セーフティネットのフックがセッション終了時（`SessionEnd`）と compaction の前（`PreCompact`）に、`.claude/knowledge/` と `.claude/tasks/` の変更だけをコミットします。`git add -A` は実行せず、push もしません。漏えいしやすい形が差分に含まれる場合は leak-scan ゲートがコミットを止めます（`CCMEMO_AUTOCOMMIT_ON_LEAK=warn` にすると警告つきでコミットします）。これは手動のセッション終了コミットの置き換えではなく補完なので、すでにコミット済みなら何もしません。

## Git リポジトリに置く理由

すべてがリポジトリ内の素の Markdown なので、コードと同じブランチ、マージ、レビューの流れに乗ります。

- **チーム共有**：月曜に誰かが見つけた落とし穴を、火曜には全員（とすべての Claude Code セッション）が参照できます。
- **セッション継続**：プランとナレッジがセッションをまたいで残るため、文脈の説明し直しや、同じ問題の再発見が要りません。
- **閲覧性**：特別なツールなしで、GitHub や GitLab の上でそのまま読めます。
