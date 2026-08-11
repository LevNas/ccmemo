# 導入ガイド

> English version: [getting-started.md](getting-started.md)

リポジトリの配置から、ユーザースコープでのインストール、最初のエントリを記録して別セッションで呼び出すまでを一続きで案内します。
この内容の圧縮版が README の [Quick start](../README.md#quick-start) です。
日々の使い方は [usage.ja.md](usage.ja.md) にあります。

## このガイドで得られる状態

- ccmemo を一度インストールするだけで、開くすべてのリポジトリで使える
- 各リポジトリが `.claude/knowledge/` に自分のナレッジベースを持つ、予測可能なクローン配置ができている
- 最初のエントリを記録してコミットし、新しいセッションがそれを呼び出せる

## 前提

- [Claude Code](https://code.claude.com/docs/en/overview) CLI
- `git`（エントリは Markdown ファイルとしてリポジトリにコミットします）
- `rg`（[ripgrep](https://github.com/BurntSushi/ripgrep)）と `jq` と [`mecab`](https://taku910.github.io/mecab/): 毎プロンプトで関連エントリを自動提示するフックは、3つすべてが `PATH` にあるときだけ動きます（欠けていると**黙って何もしません**）。`rg` は `/recall-knowledge` の索引なしフォールバックの土台でもあります
- 任意: `python3`（リンクグラフ CLI 用。オプトイン）
- 任意: [`uv`](https://docs.astral.sh/uv/)（意味検索用の最短ランナー。オプトインで、`python3` + pip でも代替可。手順5参照）
- 任意: [ghq](https://github.com/x-motemen/ghq)（後述のクローン配置を自動化）

## 手順1: リポジトリを配置する

ccmemo はナレッジを各リポジトリの内側（`.claude/knowledge/`）に置きます。
そのためナレッジはコードと同じ経路をたどります。
ブランチも、レビューも、クローンも、ナレッジベースごと運ばれます。

複数のリポジトリがそれぞれナレッジベースを持つようになると、どこに何のナレッジがあるかを覚えておく代わりに、配置の規則から導ける状態が効いてきます。
そこで `~/src/<ホスト>/<オーナー>/<リポジトリ>` の配置を推奨します。

```
~/src/
└── github.com/
    └── you/
        ├── app     ← ナレッジベースは ~/src/github.com/you/app/.claude/knowledge/
        └── infra   ← ナレッジベースは ~/src/github.com/you/infra/.claude/knowledge/
```

素の `git clone` で作れます。

```bash
git clone https://github.com/you/app ~/src/github.com/you/app
```

[ghq](https://github.com/x-motemen/ghq) はまさにこの配置を自動化するツールです。
管理するクローンが数個を超えたら導入する価値があります。

```bash
git config --global ghq.root '~/src'
ghq get github.com/you/app        # ~/src/github.com/you/app にクローンされる
ghq list                          # 全リポジトリを1行ずつ列挙
```

ghq は任意で、ccmemo は ghq に依存しません。
ただし `ghq list` を手元のファジーファインダーにつなぐと、「リポジトリを開けば、そのナレッジも手元にある」という状態をリポジトリ間で素早く行き来できます。

## 手順2: ユーザースコープでインストールする

任意の Claude Code セッションで実行します。

```
/plugin marketplace add LevNas/claudecode-plugins
/plugin install ccmemo@levnas-plugins
```

スコープを聞かれたら **User** を選びます。
プラグインは `~/.claude/` 配下に一度だけインストールされ、開くすべてのリポジトリで有効になります。
本ガイドはこの状態を前提にします（より狭い範囲で使うための Project スコープと Local スコープもあります）。

シェルから非対話でインストールする場合は次のとおりです。

```bash
claude plugin install ccmemo@levnas-plugins --scope user
```

インストール結果に `Run /reload-plugins to activate` と表示されたら `/reload-plugins` を実行します（新しいセッションを開き直しても同じです）。
有効になったことは次の2点で確認できます。

- `/plugin list` に ccmemo が表示される
- `/ccmemo:` と入力すると4つのスキル（`record-knowledge`、`recall-knowledge`、`review-knowledge`、`plan-task`）が補完される

ここから先、スキルと Context Guard フックは有効です。
リポジトリごとのインストール作業はありません。

**チーム向けの補足**: 共有プロジェクトを開いた全員に ccmemo を自動で有効化するには、プロジェクトの `.claude/settings.json` に次をコミットします（各メンバーはマーケットプレイス追加の1行だけ実行しておきます）。

```json
{
  "enabledPlugins": {
    "ccmemo@levnas-plugins": true
  }
}
```

## 手順3: リポジトリをスキャフォールドする

リポジトリごとに一度だけ、初期設定（ナレッジのタグ台帳とタスクの索引）を生成します。
リポジトリのルートで開いたセッションで、次のように頼むだけです。

> Scaffold ccmemo's knowledge and tasks templates into `.claude/`.

これで次の構成が作られます。

```
.claude/
├── knowledge/   （タグ台帳の CLAUDE.md と entries/）
└── tasks/       （CLAUDE.md と readme.md 索引）
```

頼む代わりにシェルコマンドで済ませたい場合は [usage.ja.md](usage.ja.md) の「シェルでのスキャフォールド」節を参照してください。

## 手順4: 記録して、コミットして、呼び出す

1. **記録する。** `/record-knowledge` を実行し、残す価値のあることを説明します。踏んだばかりの落とし穴、決定とその理由、ドキュメントにない癖などです。Claude がタグ付き Markdown エントリを `.claude/knowledge/entries/` に書きます。
2. **コミットする。** エントリはただのファイルなので、コードと同じようにコミットします。チームならブランチとレビューの流れにそのまま乗ります。
3. **呼び出す。** 新しいセッションを開いてその話題を尋ねるか、`/recall-knowledge` を明示的に実行します。1セッションで消えるはずだった知見が、リポジトリの一部になっています。

この「記録して、コミットして、呼び出す」のループが ccmemo の中核です。
他の機能はすべて、このループの効きを高めるためにあります。

## 手順5（任意）: 意味検索を有効にする

`/recall-knowledge` は準備なしでも ripgrep のみのモードで動作します。
意味での検索（同義語や、英語の識別子に対する日本語クエリ）まで使うには、マシンごとに一度、ローカルのベクトル索引を構築します。

```bash
# 派生キャッシュである索引を git 対象外にする（リポジトリごとに一度）
echo '.claude/knowledge/.index/' >> .gitignore

# 索引を構築する。依存（fastembed + sqlite-vec）は uv が一時環境へ自動導入し、
# 埋め込みモデルは初回実行時に一度だけダウンロードされる。処理は完全に
# ローカルで、ナレッジ本文が外部へ送られることはない。
scripts=$(find ~/.claude/plugins/cache -type d -path '*ccmemo*/scripts' | sort | tail -1)
uv run "$scripts/kb_index.py" .claude/knowledge/entries/
```

クエリを1本投げて動作確認します。

```bash
uv run "$scripts/kb_search.py" .claude/knowledge/entries/ "探したい内容"
```

これ以降、`/recall-knowledge` は索引を自動で使います。
索引はマシンごとの派生キャッシュです。再実行は差分のみを埋め込み直し、検索時にも変更分が遅延更新され、いつでも作り直せます。
`uv` を使わない場合は `pip install 'fastembed>=0.3' 'sqlite-vec>=0.1.6'` と `python3` でも動きます。
その手順、チーム運用で `git pull` 後に索引を追随させる post-merge フック、NixOS での注意は [hybrid-search.md](hybrid-search.md)（英語）にあります。

## 次に読むもの

- [usage.ja.md](usage.ja.md): エントリの検索、ナレッジベースのレビュー、プランとタスク、カスタマイズ
- [hybrid-search.md](hybrid-search.md): 意味検索の詳細（検索パイプラインとフィルタ、pip 経路、post-merge での索引追随、NixOS）
- [link-graph.md](link-graph.md): `see:` リンクグラフの構造クエリ（ハブ、孤立エントリ、最短経路）
- [claude-md-examples.ja.md](claude-md-examples.ja.md): プロジェクトの CLAUDE.md への組み込みパターン

## ccorch でスケールさせる

ccmemo 単体がカバーするのは、1セッションで完結する記録と呼び出しのループです。
同じマーケットプレイスの姉妹プラグイン [ccorch](https://github.com/LevNas/ccorch) はサブエージェントオーケストレーションを加えます。
同梱される9種のエージェント型のうち、次の2つは ccmemo 形式のナレッジベースに合わせて作られています。

- **`ccorch:kb-integrator`**：10件以上のエントリを読み、出典付きの統合を返します。エントリが手で読み返せる量を超えても、「X について何をすでに知っているか」の確認が一問で済み続けます。
- **`ccorch:knowledge-recorder`**：`/record-knowledge` の規約に沿ってエントリを起草します。大きな作業セッションの発見を並列で下書きさせつつ、何を記録するかの判断は自分の手に残せます。

2つを併用すると、どちらか単体では閉じないループが閉じます。
スケールした作業をオーケストレーションし、各ウェーブの発見を記録し、次に判断する前に呼び出す、という循環です。
セッションの合間は ccmemo の `/plan-task` がつなぎます。

```
/plugin install ccorch@levnas-plugins
```

ccorch 側のセットアップは [ccorch の導入ガイド（日本語）](https://github.com/LevNas/ccorch/blob/main/docs/getting-started.ja.md) を参照してください。
