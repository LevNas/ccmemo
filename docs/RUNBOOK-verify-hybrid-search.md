# Runbook: ハイブリッド検索 縦切りの検証（人が実シェルで実施）

サブエージェントはサンドボックスでコード実行・ネットワーク・他repo書込が不可だったため、
以下は**ネットワークのある実シェル**で人が実施する。スクリプトは ccmemo 作業ツリー内に
**未コミットで配置済み**（`scripts/` 等）。そこから直接動かして**検証してから**コミット判断する。

検証対象の的: `op-wrap` / `glab-op` を含むエントリ
`notebooks/.claude/knowledge/entries/2026/06/20260610-134028-hasato-op-run-token-injection-wrappers.md`
（本文は「トークン/認証」と書かれ「シークレット/credential」を**使っていない** → rg は同義語で取りこぼす）

---

## 変数（コピペ用）

```bash
KB=/home/hasato/src/github.com/LevNas/notebooks/.claude/knowledge/entries
SCRIPTS=/home/hasato/src/github.com/LevNas/ccmemo/scripts
```

## 前提ツール確認

```bash
uv --version        # mise 管理で導入済みのはず
mecab --version
rg --version
```

無ければ: `uv` は mise（`mise install uv`）、`mecab` は OS パッケージ。

---

## Step 0. index を git に載せない保護（先にやる）

notebooks は `.claude/` をコミット対象にしているため、派生インデックスが誤って
commit されないよう **先に** ignore する。

```bash
cd /home/hasato/src/github.com/LevNas/notebooks
grep -qxF '.claude/knowledge/.index/' .gitignore 2>/dev/null \
  || echo '.claude/knowledge/.index/' >> .gitignore
git check-ignore .claude/knowledge/.index/kb.db   # → パスが表示されれば無視OK
```

---

## Step 1. 依存導入 + モデルDL + index ビルド（初回・ネットワーク必須）

`uv run` は PEP 723 のインラインメタdata を読み、**(a) fastembed/sqlite-vec を一時環境に
インストール**し、**(b) 初回はモデル ~100MB を取得**してから索引化する。3つが1コマンドで起きる。

```bash
uv run "$SCRIPTS/kb_index.py" "$KB"
```

- 期待出力例: `index: +142 ~0 -0 =0 (… chunks embedded) -> …/.claude/knowledge/.index/kb.db`
- 初回は依存解決とモデルDLで時間がかかる。2回目以降は increment（変更分のみ）で高速
- 生成物: `notebooks/.claude/knowledge/.index/kb.db`
- 冪等: 変更なしで再実行すると `+0 ~0 -0`、何も embed しない

### トラブル時
- ネット遮断/プロキシ → モデルDL が失敗。社内 TLS インスペクション環境なら証明書設定が要る
  （cf. ナレッジ `npm-self-signed-cert-windows-tls-inspection` と同系統の事象）
- `uv` が無ければ `pip install 'fastembed>=0.3' 'sqlite-vec>=0.1.6'` 後に
  `python3 "$SCRIPTS/kb_index.py" "$KB"`

---

## Step 2. rg ベースライン（取りこぼしを観測）

同義語クエリで rg が的を外すことを確認する。

```bash
rg -l 'シークレット'  "$KB" | grep token-injection   # → 何も出ない（取りこぼし）
rg -l 'credential'    "$KB" | grep token-injection   # → 何も出ない（取りこぼし）
rg -l 'op-wrap'       "$KB"                            # → 1件（完全一致なら rg でも引ける）
```

期待: 同義語（シークレット / credential）では **的エントリが0件**。これが lexical 単独の限界。

---

## Step 3. hybrid 検索（同義語でも的が上位に来るか）

```bash
# rg が取りこぼした同義語で検索
uv run "$SCRIPTS/kb_search.py" "$KB" "シークレット注入のラッパー" --top 8
uv run "$SCRIPTS/kb_search.py" "$KB" "トークン注入のラッパー"   --top 8

# フィルタ併用例
uv run "$SCRIPTS/kb_search.py" "$KB" "認証情報の注入" --status active --tag '#secret-management' --top 8 --json
```

- 確認点: 出力上位に
  `…2026/06/20260610-134028-hasato-op-run-token-injection-wrappers.md` が来ること
- `--json` で機械可読出力。`--no-lazy`（検索前の自動再embed抑止）、`--no-mecab`（mecab前処理抑止）も可

---

## Step 4. 合否判定

| 観点 | 合格基準 |
| --- | --- |
| 同義語リコール | rg が0件の「シークレット/credential」系クエリで、hybrid は的エントリを上位に出す |
| 完全一致 | `op-wrap` 等の識別子そのままなら lexical アームで確実に拾う（劣化していない） |
| 冪等性 | `kb_index.py` 再実行が `+0 ~0 -0` |
| index 非コミット | `git check-ignore` でヒット、`git status` に `.index/` が出ない |

- **合格** → 配置・配線・コミットの判断へ（下記）
- **不合格/微妙** → チャンク粒度・RRF重み・mecab前処理をチューニング（メインブレインに戻して相談）

---

## 検証後（合格時のみ・別途判断）

1. 配置済みファイル（`scripts/*.py`, `hooks/post-merge.sample`, `docs/*`）の最終構成を確定。
   **要判断: docs を README 節にするか別ファイルのままか**
2. consumer の `.gitignore` に `.claude/knowledge/.index/`（Step 0 で notebooks は対応済み）
3. **要判断: consumer でのスクリプト設置場所**（post-merge フックの解決パス。現 install フローは
   skills/templates のみコピーのため未定義）
4. 既存 `userpromptsubmit_knowledge_search.sh` への配線
5. コミットは英語メッセージ・ユーザー承認後（公開リポジトリ方針）

> 注: 検証段階では ccmemo に何も置かず、コミットもしない。
