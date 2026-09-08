# TeamAIとの境界

2026-09-08に Tencent/teamai-cli の `0ec7b77b3663b9f72bb8b0b4532e722be6c1c159`（package.json: 0.22.0）を確認しました。

## 管理対象

個人側はフィードバック検出、振り返り、個人設定を管理します。
チーム側は成果物・開発手順・共通知識を管理します。共有側から `personal-*` を配布しません。
チームに採用する知見は、個人メモリのコピーではなく通常のPRで提案します。

TeamAIはプロジェクトスコープで導入します。ただしユーザーとプロジェクトのHooksは両方実行されるため、スコープだけでは二重通知を解消できません。
個人用インストーラーはTeamAIの登録に触れません。

## 現時点の制約

- `sharing.recall.enabled: false` は自動検索を無効にします。
- `sharing.hooks.autoApply: false` と `TEAMAI_HOOKS_DISABLED=1` はチームが配布するカスタムHooksの制御です。内蔵Hooksの停止設定ではありません。
- `src/hook-handlers.ts` の `buildHandlerRegistry()` は、Stopへ `contribute-check`、UserPromptSubmitへ `pending-hint` を登録します。
- `src/contribute-check.ts` の `contributeCheckForSession()` と `takePendingHint()` には、個人の振り返りへ委譲するための無効化設定がありませんでした。

したがって、標準版との共存では保存先を分離できますが、共有提案の通知が二重になる可能性は残ります。
個人側のルールでTeamAIのスクリプト実行そのものを止められるとは扱いません。
確認対象: [Hooks登録](https://github.com/Tencent/teamai-cli/blob/0ec7b77b3663b9f72bb8b0b4532e722be6c1c159/src/hook-handlers.ts)、[共有提案](https://github.com/Tencent/teamai-cli/blob/0ec7b77b3663b9f72bb8b0b4532e722be6c1c159/src/contribute-check.ts)、[設定スキーマ](https://github.com/Tencent/teamai-cli/blob/0ec7b77b3663b9f72bb8b0b4532e722be6c1c159/src/types.ts)。

## 完全分離に必要な変更

TeamAI側に、共有提案の生成と保留済み提案の配信を止める設定を追加するのが最小です。
対象は上記2関数です。Stop全体を止めると利用状況集計なども止まるため、対象を絞ります。
手動の知見共有、設定配布、recall、他のHooksはそのまま使える必要があります。
これは上流への変更案であり、このリポジトリはTeamAIをforkしたり、インストール済みコードを書き換えたりしません。

チーム展開前に「当面は通知重複を許容する」「上流に停止設定を追加してから導入する」のどちらかを決めます。
個人用の導入と検証は、その判断を待たずに進められます。
