# 既存の自己改善機構から移行する

既存環境を Git で公開するのではなく、この配布リポジトリだけを公開します。
設定バックアップや過去の Git 履歴にも、本人や組織の情報が含まれ得ます。

## 引き継ぐもの

| 既存の要素 | 移行後 |
|---|---|
| Stop のフィードバック検出 | 共通の `loop.py` へ |
| SessionStart の改善キュー通知 | 共通の `loop.py` へ |
| 旧 `retro/queue.md` の残件 | `--retire-legacy` が transcript を再走査して新しいキューへ取り込む |
| `/retro` の分析・原因分類・保存先の判断 | `personal-retro` へ |
| memory-learner / agent-improve の抽出・修正規律 | `personal-retro` に必要な規律を統合。専用エージェントは要求しない |
| 既存の個人メモリ・プロファイル | 元の場所に保持。自動コピーしない |
| 横断 knowledge | `pal knowledge use <path>` で登録。中身は動かさない |
| コンパクション復旧・その他の Hook | 既存の登録を保持 |

## 切り替え

対話セットアップが旧 Hook を検出し、置き換えと退避を順に確認します。

```bash
python3 install.py
```

フラグで指定する場合は次のとおりです。1行目で変更予定を確認し、2行目で適用します。

```bash
python3 install.py --tool claude --tool codex --replace-legacy --retire-legacy --knowledge-use ~/knowledge
python3 install.py --tool claude --tool codex --replace-legacy --retire-legacy --knowledge-use ~/knowledge --apply
```

`--replace-legacy` は、各ツールの設定ディレクトリ直下 `hooks/` にある次のスクリプトを `bash <絶対パス>` で呼ぶ登録だけを外します。

- `stop-feedback-detector.sh`
- `sessionstart-improvement-notice.sh`

`--retire-legacy` は、上の2スクリプトと `hooks/lib/extract-feedback.sh`、`retro/`（旧キューと処理済み台帳）、`skills/retro/` を、ローカルデータの `backups/<日時>/legacy/<tool>/` へ移動します。
移動の前に `retro/queue.md` の各行から transcript を読み直し、現行の検出ルールで候補を新しいキューへ入れます。
旧キューは作業パスを持たないため、transcript 内の記録から補います。
transcript が消えている行は `missing` として報告し、内容は推測しません。

ラッパーや別名、別ディレクトリの旧 Hook は自動識別しないため、変更予定と実際の設定を照合してください。
設定ファイル単位の更新は atomic ですが、複数ファイルの一括トランザクションではありません。
I/O エラーが出た場合はバックアップと設定を確認して再実行してください。
別のプロセスが設定を書き換えている最中の導入・削除は避けてください。

## 既存の指示ファイルの整理

既存の AGENTS.md / CLAUDE.md にある次の指示は、その環境の正本を確認して整理します。

- 作業完了やフィードバックのたびに memory-learner を自動起動する指示は、新しい振り返り経路と二重に走らせない
- `/retro` を呼ぶ指示は `personal-retro` に置き換える
- 横断 knowledge の場所を書いた指示は、Hook が毎セッション伝えるため重複する。残すなら昇格の申し出ルールだけにする
- 本人専用パス、特定モデル、他環境への自動還流は公開スキルへ持ち込まない
- 個人メモリ・ユーザープロファイルを公開側へコピーしない

インストーラーは自由記述の指示ファイルを自動置換しません。元の意図を壊す誤置換を避けるためです。

## 戻す

新しい登録は `install.py --uninstall --apply` で取り除けます。
旧 Hook を戻す場合は、表示されたバックアップから該当する2つの登録だけを現在の設定へ戻し、`backups/<日時>/legacy/` のファイルを元の場所へ移します。
バックアップを丸ごと上書きすると切り替え後の他の変更を失うため、差分を確認してください。
