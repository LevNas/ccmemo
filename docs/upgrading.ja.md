# アップグレード

> English version: [upgrading.md](upgrading.md)

既存のナレッジベースを新しい ccmemo へ移す手順。利用者側の作業が要るバージョンだけ節を設け、それ以外は更新するだけで済む。[CHANGELOG](../CHANGELOG.md) の該当リリースからここへリンクしている。

## 更新が届く仕組み

- プラグインのキャッシュは `plugin.json` の `version` をキーに保存される。`/plugin update ccmemo` はその値が変わったときだけ新版を取得し、`/reload-plugins` で実行中のセッションに反映される。
- ハイブリッド検索の索引（`.claude/knowledge/.index/kb.db`）は派生キャッシュ。スキーマが変わっても、次の検索か `kb_index.py` の実行時に Markdown から自動で移行される。再埋め込みも手動の再構築も要らない。
- エントリの規約はプラグインとは別に版管理する。ナレッジベースは自分が従う規約の版を `.claude/knowledge/CLAUDE.md` の frontmatter で宣言する。

  ```yaml
  ---
  schema_version: 2
  ---
  ```

  新しいスキーマで加わった検査は、宣言が古いコーパスでも `kb_graph.py lint` と書込直後の hook が**一覧には出す**が、**advisory**（参考）扱いで終了コードには影響しない。宣言を上げた時点から強制になる。したがってプラグインを更新しただけで pre-commit の lint が赤くなることはなく、コーパスの移行と宣言の更新は準備ができてから行えばよい。
  `kb_graph.py --schema 2 lint` で宣言を上げたときの結果を先に確認できる。`CCMEMO_SCHEMA_VERSION=2` はシェル単位で同じ効果を持つ。

## 1.26.x — `description` とリンクのラベルが規約になる（schema_version 2）

検索結果（`kb_search.py --summary`）とプロンプト hook は、各エントリの `description`（トリガー条件＝どういう状況で開くか）を表示するようになった。description が無いエントリは本文の冒頭段落を `(lead)` 印付きで代替するので壊れはしないが、要約の目的（複数の候補を開かずに 1 本を選ぶ）は description が揃って初めて機能する。

`kb_graph.py lint` に 4 つの検査が加わった。`schema_version: 2` で強制、それ未満では advisory: `missing-description`、`description-length`（80〜320 字）、`unlabeled-link`（`see:` / `ref:` / `amends:` / `extends:` 行でリンクの後ろに何も無い）、`amends-` / `extends-unreciprocated`。`/record-knowledge` で書く新規エントリには最初から description が付く。

### 既存コーパスの移行

1. 現状を把握する。
   `python3 scripts/kb_graph.py --root .claude/knowledge/entries --schema 2 lint`
   で、宣言を上げたときに違反になるエントリが一覧できる。`kb_graph.py index-md` は description 未付与の件数を出す。
2. description をバッチで付ける。description は要約ではない（結論はタイトルがすでに述べている）。将来の読み手がそのエントリを開くべき状況、つまり症状・問い・下そうとしている判断を、最も典型的なものから順に 100〜300 字で「〜のときに開く」の形に書く。ハブ型（`synthesis` / `overview`）は末尾に「〜に関する派生エントリへのハブでもある」を添える。本文の問題・背景の節から書き起こし、タイトルの複写や値の中の双引用符は避ける。参照コーパス（280 件）では 28 件 × 10 バッチを Sonnet のサブエージェントに委譲し（各エントリの冒頭 60〜80 行を読ませる）、バッチごとに機械検算（全件にフィールドがある・共通パーサで読める・長さが範囲内・`"` を含まない）を挟んだ。基準と良い例 5 件を 1 ファイルにまとめ、各バッチに渡すとよい。
3. リンクにラベルを付ける。`- see:` / `ref:` / `amends:` / `extends:` の各行はリンクの後ろに `— なぜ辿るか` を**同じ行に**書く（lint は 1 行単位で読むので、次の行に折り返したラベルは無いものと扱われる）。`amends:` / `extends:` の相手側には逆リンクが要る（相手がこのエントリに supersede されている場合も可）。
4. `--schema 2` の lint が 0 件になったら `.claude/knowledge/CLAUDE.md` に `schema_version: 2` を宣言する。以後は検査が強制され、書込直後の hook でも保存のたびに検査される。

[link-graph.md](link-graph.md) の手順で pre-commit に lint を配線している場合も、宣言を上げるまで advisory は終了コードを変えないので、移行中もコミットは通る。

redact hook を有効にしたまま一括編集する場合の注意: 全エントリが `postwrite_redact_entries.py` を通るため、本文に元から残っていた秘密情報を捕まえる機会にもなる（参照コーパスでは 1Password の生アイテム ID と個人メールアドレスが見つかった）。一方で、systemd のユニット名（`app-…@autostart.service`）やプレースホルダ付き SSH URL のようなメール形の文字列を過検知することがある。戻すときは Edit ツールではなくシェルで編集する（Edit だと hook が再発火する）。

## 1.24.0 — frontmatter パーサの統一、`tags` のリスト形式、`status` の既定

索引・検索・グラフ CLI・プロンプト hook のすべてが同じパーサを使うようになった。既存エントリに影響するのは次の 2 点で、どちらも書き換えは不要。

- YAML リスト形式の `tags:` がすべての読み手で読めるようになった（以前はスクリプト側でリスト形式が黙って「タグ無し」になっていた）。新規エントリの正典はリスト形式。1 行形式の `tags: "#a #b"` も引き続き完全に対応するので、既存エントリを一括で書き換えないこと。
- `status:` が無い、または空のエントリは `kb_search.py` / `kb_index.py` でも `active` 扱いになった（プロンプト hook は以前からそうだった）。status の無いエントリが `--status active` から漏れなくなる。
- 数字で始まるタグ（例: `#1password`）が認識されるようになった。回避策を入れていた場合は外してよい。
