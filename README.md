# personal-ai-loop

Claude Code や Codex で受けた修正・承認を、自分の環境の改善へつなげる小さな仕組みです。
公開するのは検出・振り返り・導入の手順だけで、会話ログや学習内容は各自の PC に残します。

```text
作業中の修正・承認 → ローカルの改善キュー → personal-retro
                                         ├─ 個人設定（personal.md）
                                         ├─ プロジェクトメモリ（AI ツール既存のもの）
                                         ├─ 個人用 knowledge リポジトリ
                                         ├─ チームへのレビュー付き提案
                                         └─ 配布機構への修正案
```

チームの共有資産は [TeamAI](https://github.com/Tencent/teamai-cli) で配ります。
その雛形は [team-ai-starter](https://github.com/Rererr/team-ai-starter) にあり、この仕組みと役割を分けています。

## 導入

Python 3.9 以上と Git 2.28 以上、macOS または Linux が前提です。Python 標準ライブラリだけを使います。
このリポジトリを任意の場所へ clone し、そのディレクトリで対話セットアップを実行します。

```bash
git clone https://github.com/Rererr/personal-ai-loop.git
cd personal-ai-loop
python3 install.py
```

対話セットアップは次の三つを順に確認し、変更予定を表示してから適用します。

1. 導入する AI ツール（`~/.claude`、`~/.codex` を自動検出）
2. 旧フィードバック機構の有無（検出したら置き換えと退避を提案）
3. 個人用 knowledge リポジトリ（既存を使う、clone する、新規作成、今は設定しない）

適用すると、各ツールに SessionStart と Stop の Hook を1つずつ登録し、`personal-retro` と `personal-setup` のスキルをリンクします。
既存の Hook や設定項目は保持し、変更前の設定をローカルデータ内へバックアップします。
同名のスキルや別の導入元が存在する場合は上書きせず終了します。
あわせて `~/.local/bin/pal` にコマンドを置きます。`~/.local/bin` が PATH に無ければ、表示される絶対パスで呼べます。

端末を使わない導入や、AI に導入を頼む場合はフラグで指定します。

```bash
python3 install.py --tool claude --tool codex
python3 install.py --tool claude --tool codex --apply
python3 install.py --tool claude --root claude=$HOME/.claude-work --apply
```

`--apply` が無い実行は変更予定の表示だけです。
設定ディレクトリが既定と違う場合は `--root tool=PATH` で指します（`CLAUDE_CONFIG_DIR`、`CODEX_HOME` の環境変数も読みます）。

再起動後、Codex では `/hooks` で追加された Hook を確認して信頼してください。
信頼状態やサンドボックスの権限はインストーラーから変更しません。
Hook にローカルデータへの書き込み権限が必要です。

## 普段の使い方

Stop Hook が修正・承認らしい発言を検出し、次のセッション開始時に対象プロジェクトの残件を AI へ通知します。
振り返りたいときに「personal-retro で振り返って」と依頼してください。
自動でルールやメモリを書き換えたり、外部へ送信したりはしません。

何が入っていて、何が溜まっているかは1コマンドで見えます。

```bash
pal status
```

導入先ツールと Hook、振り返り候補の件数、`personal.md` の大きさ、knowledge リポジトリの状態、TeamAI の接続状況、旧機構の残りを表示します。
`--json` を付けると機械可読になります。

```bash
pal queue --cwd "$PWD"
pal inspect 3
pal ack 3
```

発言の本文はキューへ複製せず、セッション、作業パス、transcript の場所、照合用ハッシュを保存します。
`inspect` は元の transcript を読んで根拠を表示します。
DB には機密性のあるパスが含まれ得るため、公開対象にしないでください。

## 個人用 knowledge リポジトリ

**個人用 knowledge リポジトリ**は、プロジェクトを跨いで再利用する規則や手順を置く、本人専用の Git リポジトリです。
AI ツールのメモリは機械ごと、プロジェクトごとに散らばりますが、ここに置いた規則は環境から独立して持ち運べます。
セットアップで登録すると、セッション開始時に AI へ場所と読み方を伝えます。

```bash
pal knowledge init ~/knowledge --remote git@github.com:you/knowledge.git
pal knowledge clone git@github.com:you/knowledge.git ~/knowledge
pal knowledge use ~/existing-knowledge
pal knowledge check
```

新規作成では、索引付きの README、書き込み規約（AGENTS.md）、三つのカテゴリ（patterns、decisions、runbooks）、frontmatter と索引の整合を検査するスクリプト、pre-commit フックを配置して初回コミットまで行います。
リモートの作成は行いません。`gh repo create --private` などで作ってから `--remote` に渡してください。
`check` はリポジトリ側の検査スクリプトを実行します。検査規則の正本をリポジトリに置くのは、他の機械や他のツールからも同じ検査が走るようにするためです。
既存のリポジトリを `use` で登録する場合は README.md があれば受け入れ、`githooks/` があれば pre-commit を有効にします。

## 3つの置き場

| 場所 | 管理するもの | 公開・共有 |
|---|---|---|
| このGitリポジトリ | Pythonコード、スキル、テンプレート、導入手順 | public にできる |
| `~/.local/state/personal-ai-loop/` | キュー、処理済み記録、`personal.md`、`config.json`、共有前の草案、設定と旧機構のバックアップ | 本人の PC だけ |
| 個人用 knowledge リポジトリ | 本人が採用した横断的な規則・手順・技術選定 | 本人の private リポジトリ |
| チームリポジトリ | レビューで採用した共通ルール・スキル・知識 | チームへ共有 |

`personal.md` は本人が採用した短い汎用設定だけを置く任意ファイルです。
セッション開始時に最大 8,000 文字を読みます。
Codex は Hook の追加コンテキストを既定で約 2,500 トークンに制限するため、Codex 側の Hook には上限の引き上げを登録しています。
プロジェクト固有の記憶は AI ツール既存のメモリへ保存し、別のメモリエンジンは作りません。
自分の調整は `personal.md` に保存し、配布スキルを直接書き換えないでください。

## 更新・削除

導入先は clone へのリンクです。移動・削除せず維持してください。
自動更新はありません。差分を確認してから更新します。

```bash
git fetch origin
git diff HEAD..origin/main
git merge --ff-only origin/main
python3 install.py --tool claude --tool codex --apply
```

ローカルデータと配布コードは別のため、コード更新で学習内容を上書きしません。
Hook のコマンドが変わらなくても実行コードは更新されます。更新内容の確認は必要です。

```bash
python3 install.py --tool claude --tool codex --uninstall --apply
```

登録した Hook、スキルのリンク、`pal` コマンドだけを外します。
学習データ、バックアップ、knowledge リポジトリ、導入元へのリンクは残します。
誤って消さないため、ローカルデータを削除するコマンドは用意していません。

## チームと使う

新メンバーに案内するのは「このリポジトリ」と「チームの共有リポジトリ」の2つです。
チーム側は TeamAI のプロジェクトスコープで配布できます。
名前が `personal-` で始まるスキルは個人側の管理領域とし、チームから同名で配布しません。
個人のキューやメモリからチームへ全文を自動送信せず、一般化した変更だけを PR へ昇格します。

TeamAI にも「セッションの摩擦」を検出して共有を促す Hook があり、標準設定では両方の案内が出ます。
どちらを入口にするか、TeamAI 側で何を止められるかは [TeamAI との境界](docs/teamai.md) にまとめました。

## 既存環境からの移行

[移行手順](docs/migration.md) を参照してください。
対話セットアップが旧 Hook を検出し、旧キューの取り込みと旧ファイルの退避まで行います。

## 検証と制限

```bash
python3 test_loop.py
```

隔離した仮のホームで、対話セットアップ、`--root` による設定ディレクトリの指定、導入・再導入・削除、他の Hook の保持、旧機構の置換と取り込み、knowledge の作成と検査、両ツールのログ形式、二重検出防止、処理済み後の追加フィードバックを検証します。
既存の日本語検出ルールを基に、英語の強い表現も少数扱います。セッション最初の発言は作業指示として除外します。意味解析ではないため、引用の誤検知や言い換えの検出漏れはあります。
Stop 時に transcript を全走査します。巨大なログで 10 秒を超える場合は Hook がタイムアウトし得ます。
ログの置換や並べ替えは再検出を起こし得ます。検出結果は確定した学習ではなく、振り返り候補です。
Windows と、Claude Code・Codex 以外のツールへの接続は未対応です。

Codex の transcript 形式は安定した API ではありません。互換形式と `event_msg` / `response_item` 形式を扱い、未知のレコードは読み飛ばします。
導入後は実際のセッションでも候補が記録されることを確認してください。
参考: [Codex Hooks](https://developers.openai.com/codex/hooks)、[Claude Code Hooks](https://code.claude.com/docs/en/hooks)。
