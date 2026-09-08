# TeamAI との境界

2026-09-08 に Tencent/teamai-cli の `0ec7b77b3663b9f72bb8b0b4532e722be6c1c159`（package.json: 0.22.0）を確認し、同日の 0.23.0-beta.8 で再確認しました。
ライセンスは MIT です（LICENSE に Tencent の著作権表示と MIT 本文）。
fork は行いません。必要だった変更は後述の2点で、いずれも上流へ PR として出し、同日にマージされました。

## 管理対象

個人側はフィードバック検出、振り返り、個人設定、個人用 knowledge を管理します。
チーム側は成果物、開発手順、共通知識を管理します。共有側から `personal-*` を配布しません。
チームに採用する知見は、個人メモリのコピーではなく通常の PR で提案します。

TeamAI には個人用リポジトリの概念がありません。
`--scope user` は同じチームリポジトリを `~/` 配下へ置く指定であり、リポジトリの種類ではありません。
そのため個人用 knowledge は personal-ai-loop 側で持ち、TeamAI には接続しません。

TeamAI はプロジェクトスコープで導入します。ただし Hook はホーム側の設定へ書かれ、ユーザーとプロジェクトの Hook は両方実行されます。
個人用インストーラーは TeamAI の登録に触れません。

## 二重通知の実態

TeamAI の Stop Hook は、セッションの「摩擦」（割り込み、ツール実行の拒否、訂正、ツールの失敗）を点数化し、閾値を超えると `/teamai-share-learnings` を促します。
personal-ai-loop の Stop Hook は、修正・承認らしい発言を検出してローカルのキューへ積みます。
同じセッションで両方が反応すると、共有の案内と振り返りの案内が並びます。

実際には次の理由で、重なる場面は限られます。

- TeamAI の案内はツール呼び出し 15 回以上のセッションに限られ、1 セッション 1 回です
- 0.22.0 までは訂正キーワードが中国語と英語だけで（`src/types.ts` の `CORRECTION_KEYWORDS`）、日本語の訂正は点数に入りませんでした。0.23.0-beta.7 から日本語も数えられます

0.22.0 で日本語で作業するチームでは、TeamAI 側の摩擦検出はほぼ鳴らず、personal-retro が唯一の入口になります。
0.23 以降は両方の案内が並ぶことがあります。案内が出た場合も、`rules/team-boundaries.md` の規約どおり共有の承認とは扱いません。

## TeamAI 側で止められるもの

| 対象 | 方法 | 影響 |
|---|---|---|
| 共有の案内だけ | チームリポジトリの `teamai.yaml` に `sharing.contributeHint.enabled: false`。本人の上書きはローカル設定の `contributeHintEnabled`、または `TEAMAI_CONTRIBUTE_HINT_DISABLED=1` | 0.23.0-beta.8 以降。更新確認・投票同期・ダッシュボード報告は止まらない。0.22 以前はキーが無視され案内が出続ける |
| 内蔵 Stop Hook 全体 | チームリポジトリの `hooks/hooks.yaml` に `builtin.disabled: ["Hook dispatch stop"]` | 共有の案内に加え、CLI の更新確認、recall 投票の同期、ダッシュボード報告も止まる |
| recall 関連（投票同期、TodoWrite 時の案内） | `TEAMAI_RECALL_DISABLED=1` | recall 機能だけ |
| チーム定義の Hook | `TEAMAI_HOOKS_DISABLED=1` または `sharing.hooks.autoApply: false` | 内蔵 Hook には効かない |

`team-ai-starter` の `teamai.yaml` は `sharing.contributeHint.enabled: false` を既定にし、CI の e2e で「日本語の訂正で案内が出る」「設定で案内が止まる」の両方を 0.23.0-beta.8 で確認しています。
0.22 以前で案内を止めるには Stop Hook 全体を止めることになり、更新確認と統計報告も止まります。同テンプレートの `hooks/hooks.yaml` にその設定をコメントアウトで置いています。

## 利用統計の送信

TeamAI は SessionStart の `teamai pull` のたびに、スキル利用回数、割り込み・拒否・失敗の回数、トークン量をチームリポジトリの `stats/<user>.yaml` と `votes/<user>.yaml` へ commit と push します。
会話本文や transcript は送りません。
この送信を止める設定は 0.22.0 にありません（HTTP モードのチームだけは送りません）。
チームへ展開する前に、この挙動をメンバーへ説明してください。

## 上流へ出した提案（2026-09-08 提出、同日マージ）

次の2点は personal-ai-loop 側で回避せず、上流へ PR として出しました。

1. [Tencent/teamai-cli#431](https://github.com/Tencent/teamai-cli/pull/431): `CORRECTION_KEYWORDS` に日本語の訂正表現（「違う」「やり直し」「勝手に」など）を追加。0.23.0-beta.7 に収録
2. [Tencent/teamai-cli#432](https://github.com/Tencent/teamai-cli/pull/432): `sharing.contributeHint.enabled`（チーム既定）、`contributeHintEnabled`（本人の上書き）、`TEAMAI_CONTRIBUTE_HINT_DISABLED=1` で共有の案内だけを止める設定を追加。0.23.0-beta.8 に収録

利用統計の送信を止める設定は、beta.8 の時点でもありません。

確認対象: [Hooks 登録](https://github.com/Tencent/teamai-cli/blob/0ec7b77b3663b9f72bb8b0b4532e722be6c1c159/src/hook-handlers.ts)、[共有提案](https://github.com/Tencent/teamai-cli/blob/0ec7b77b3663b9f72bb8b0b4532e722be6c1c159/src/contribute-check.ts)、[設定スキーマと訂正キーワード](https://github.com/Tencent/teamai-cli/blob/0ec7b77b3663b9f72bb8b0b4532e722be6c1c159/src/types.ts)、[内蔵 Hook の無効化](https://github.com/Tencent/teamai-cli/blob/0ec7b77b3663b9f72bb8b0b4532e722be6c1c159/src/builtin-hooks.ts)。
