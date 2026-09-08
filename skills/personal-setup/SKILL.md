---
name: personal-setup
description: personal-ai-loop の導入状態を確認し、個人用 knowledge リポジトリの作成・接続、旧機構からの移行、teamai との併用状況を本人と対話しながら整える。「セットアップ」「導入状態」「knowledge リポジトリを作りたい／つなぎたい」「移行したい」と言われたとき、および初回導入直後に使う。
---

# 個人環境のセットアップ

CLI は `~/.local/bin/pal`（無ければ `python3 ~/.local/share/personal-ai-loop/app/loop.py`）。
導入・移行は `~/.local/share/personal-ai-loop/app/install.py`。
本人の PC の設定を変えるので、変更前に予定を見せ、承認を得てから適用する。

## 1. 現状を見せる

```bash
pal status
```

読み取り専用。表示の各行が「導入先ツール／Hook／スキル」「振り返り候補」「personal.md」「knowledge」「teamai」「旧機構の残り」に対応する。
`--json` で機械可読になる。分からない項目は推測せず、表示をそのまま伝える。

## 2. 足りないものを本人に聞く

一度に全部聞かず、状態から不足しているものだけを順に確認する。

| 状態 | 確認すること |
|---|---|
| Hook 未登録・スキル未リンク | 導入するツール（claude / codex）。設定ディレクトリが既定と違えば `--root tool=PATH` |
| 旧キューあり（`retro/queue.md`） | 旧 Hook を置き換え、旧キューを取り込んで旧ファイルを退避してよいか |
| knowledge 未設定 | 既にあるか（ローカルのパス／リモート URL）。無ければ新規作成するか、作成先、origin の URL |
| knowledge の pre-commit 未設定・gitleaks 未導入 | `git config core.hooksPath githooks` と `gitleaks` 導入を案内 |
| teamai 導入済み | チームリポジトリの接続状況を伝えるだけ。teamai の設定はこのスキルで変えない |

knowledge リポジトリは「プロジェクト横断で再利用する、本人が採用を判断した規則」を置く private の git リポジトリ。
会社の情報や会話ログは入れない。用途を一言で説明してから聞く。

## 3. 予定を見せてから適用する

まず `--apply` なしで実行し、出力（変更する設定ファイル・外す旧 Hook・退避する旧ファイル・knowledge の操作）を要約して見せる。
承認後に同じコマンドへ `--apply` を付けて実行する。

```bash
python3 ~/.local/share/personal-ai-loop/app/install.py --tool claude --tool codex \
  --replace-legacy --retire-legacy \
  --knowledge-init ~/knowledge --knowledge-remote git@github.com:you/knowledge.git
```

- 既存を使う: `--knowledge-use PATH`。clone: `--knowledge-clone URL PATH`
- リモートの新規作成（`gh repo create` 等）は本人が実行する。コマンドを提示して結果を待つ
- 適用後は `pal status` で再確認し、ツールの再起動（Codex は `/hooks` で信頼）を案内する

## 4. 旧機構の取り込み結果を報告する

`--retire-legacy` は旧 `retro/queue.md` の各行から transcript を再走査して新しいキューへ入れ、旧スクリプト・キュー・`retro` スキルをバックアップへ移す。
出力の `imported`（取り込めたセッションと件数）と `missing`（transcript が消えた行）をそのまま伝える。
取り込んだ候補の処理は personal-retro に任せ、ここでは行わない。

## しないこと

- 本人の承認なしに `--apply` を付けない。旧ファイルの削除は行わない（退避のみ）
- knowledge リポジトリへノートを書かない（それは作業中の昇格や personal-retro の役目）
- teamai の init / uninstall / 設定変更を代行しない。必要なら公式手順を案内する
