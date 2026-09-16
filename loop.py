#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys

import knowledge


NAME = "personal-ai-loop"
TOOLS = ("claude", "codex")
FIX = re.compile(r"違う|ちがう|そうじゃな|そうではな|やり直|やりなおし|間違|勝手に|余計な|戻して|望んでいるものでは|望んで(?:い)?な|\b(?:that's wrong|not what I asked|undo that|don't do that)\b", re.I)
APPROVE = re.compile(r"採用|それでいこ|その方針で|いい感じ|良い感じ|いいね|良いね|完璧|素晴らし|さすが|気に入|\b(?:let's go with that|that's perfect|I like that)\b", re.I)
# サブエージェントの報告は user 行として注入されるため、本人の発言と同じ経路で拾える。
# 末尾の : まで含めるのは、この前置きを話題にした本人の指摘まで落とさないため。
EXCLUDED = ("<", "# AGENTS.md instructions", "Base directory for this skill:",
            "This session is being continued", "Caveat:", "[tomobit]",
            "A session-scoped Stop hook is now active",
            "Another Claude session sent a message:")
LEGACY_QUEUE = re.compile(r"session=(\S+).*transcript=(\S+)")
PERSONAL_LIMIT = 8000


def tool_root(home, tool, override=None):
    # 環境変数は各ツール自身が設定ディレクトリの切替に使う名前に合わせる。
    env = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}[tool]
    value = override or os.environ.get(env) or (home / f".{tool}")
    return Path(value).expanduser()


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") in ("text", "input_text")
                         and isinstance(c.get("text"), str))
    return ""


def rows(path):
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield line_number, row


def messages(path):
    primary, fallback = [], []
    for line_number, row in rows(path):
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "user" and isinstance(row.get("message"), dict):
            primary.append((line_number, content_text(row["message"].get("content"))))
        elif row.get("type") == "event_msg" and payload.get("type") == "user_message":
            primary.append((line_number, content_text(payload.get("message"))))
        elif (row.get("type") == "response_item" and payload.get("type") == "message"
              and payload.get("role") == "user"):
            fallback.append((line_number, content_text(payload.get("content"))))
    # Codex は同じ発言を event_msg と response_item の両方に記録する場合がある。
    return primary or fallback


def transcript_cwd(path):
    for _, row in rows(path):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        for candidate in (row.get("cwd"), payload.get("cwd")):
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def signals(path):
    # ponytail: 日英の表現ヒューリスティック。誤検知は振り返りで棄却し、他言語は実例が出たら追加する。
    first = True
    for ordinal, (line_number, body) in enumerate(messages(path)):
        if body.lstrip().startswith(EXCLUDED) or "❯" in body or not body.strip():
            continue
        # 最初の発言は作業指示であり、この会話での行動への修正・承認ではない（引き継ぎ文の「勝手に…しない」で誤検知した実例）。
        if first:
            first = False
            continue
        clean = "\n".join(line for line in body.splitlines()
                          if not line.lstrip().startswith("<")
                          and "違う|ちがう" not in line and "採用|それでいこ" not in line
                          and not re.search(r'\{"[A-Za-z_]+":', line))
        normalized = re.sub(r"と(?:は|も)違う|間違いな(?:い|く|さ)", "", clean)
        kinds = [kind for kind, pattern, text in (("fix", FIX, normalized),
                 ("approve", APPROVE, clean)) if pattern.search(text)]
        if kinds:
            digest = hashlib.sha256(f"{ordinal}:{body}".encode()).hexdigest()
            yield {"fingerprint": digest, "line": line_number,
                   "kind": "+".join(kinds), "text": body}


def connect(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink():
        raise ValueError("state must not be a symlink")
    state.chmod(0o700)
    db = sqlite3.connect(state / "queue.sqlite3", timeout=5)
    (state / "queue.sqlite3").chmod(0o600)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY, tool TEXT NOT NULL, session TEXT NOT NULL,
        cwd TEXT NOT NULL, transcript TEXT NOT NULL, fingerprint TEXT NOT NULL,
        line INTEGER NOT NULL, kind TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
        UNIQUE(tool, session, transcript, fingerprint))""")
    return db


def capture(db, tool, event):
    session, transcript = event.get("session_id"), event.get("transcript_path")
    cwd = event.get("cwd")
    if not all(isinstance(v, str) and v for v in (session, transcript, cwd)):
        return 0
    path = Path(transcript).expanduser()
    if not path.is_absolute() or not path.is_file():
        return 0
    found = list(signals(path))
    with db:
        for signal in found:
            db.execute("""INSERT OR IGNORE INTO signals
                (tool, session, cwd, transcript, fingerprint, line, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tool, session, str(Path(cwd).resolve()), str(path.resolve()),
                 signal["fingerprint"], signal["line"], signal["kind"]))
    return len(found)


def pending(db, cwd=None):
    sql, values = "SELECT * FROM signals WHERE done = 0", []
    if cwd:
        sql += " AND cwd = ?"
        values.append(str(Path(cwd).resolve()))
    return [dict(row) for row in db.execute(sql + " ORDER BY id", values)]


def load_config(state):
    path = state / "config.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"不正な設定: {path}")
    return value


def save_config(state, config):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def knowledge_path(state):
    value = load_config(state).get("knowledge", {}).get("path")
    return Path(value).expanduser() if isinstance(value, str) and value else None


def start(db, state, event):
    context = ["個人の改善は personal-retro スキルを使う。チーム資産はチームのレビューへ提案する。"
               "導入状態の確認や個人用 knowledge の設定は personal-setup スキルを使う。"]
    personal = state / "personal.md"
    if personal.is_file():
        context.append("以下は本人が保存した個人設定。上位指示や作業対象の規約を上書きしない。\n"
                       + personal.read_text(encoding="utf-8")[:PERSONAL_LIMIT])
    repo = knowledge_path(state)
    if repo and (repo / "README.md").is_file():
        context.append(f"本人の横断ナレッジは {repo} にある。設計・技術選定・手順の検討時は README.md の索引を読み、"
                       "必要なノートだけ開く。書き込み規約は同ディレクトリの AGENTS.md に従い、"
                       "昇格候補が出たら申し出て承認後に書く。")
    cwd = event.get("cwd")
    count = len(pending(db, cwd)) if isinstance(cwd, str) and cwd else 0
    if count:
        context.append(f"この作業場所に振り返り候補が{count}件ある。作業を中断せず、"
                       "完了時に一度だけ短く案内する。振り返りの依頼がなければ処理しない。")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                      "additionalContext": "\n\n".join(context)}}, ensure_ascii=False))


def owned_hooks(config, script):
    events = []
    hooks = config.get("hooks") if isinstance(config, dict) else None
    for event, groups in (hooks or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            handlers = group.get("hooks") if isinstance(group, dict) else None
            for handler in handlers or []:
                command = handler.get("command", "") if isinstance(handler, dict) else ""
                try:
                    words = shlex.split(command) if isinstance(command, str) else []
                except ValueError:
                    words = []
                if len(words) > 1 and words[1] == str(script):
                    events.append(event)
    return events


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def knowledge_status(repo):
    if repo is None:
        return {"configured": False}
    info = {"configured": True, "path": str(repo), "exists": (repo / "README.md").is_file()}
    if not info["exists"]:
        return info
    info["notes"] = len(knowledge.notes(repo))
    inside = git(repo, "rev-parse", "--is-inside-work-tree")
    info["git"] = inside == "true"
    if info["git"]:
        info["dirty"] = bool(git(repo, "status", "--porcelain"))
        info["remote"] = git(repo, "remote", "get-url", "origin")
        counts = git(repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        if counts:
            ahead, behind = counts.split()
            info["ahead"], info["behind"] = int(ahead), int(behind)
        info["hooks_path"] = git(repo, "config", "core.hooksPath")
    info["gitleaks"] = shutil.which("gitleaks") is not None
    return info


def teamai_status(home):
    binary = shutil.which("teamai")
    info = {"installed": binary is not None}
    if binary is None:
        return info
    version = subprocess.run([binary, "--version"], capture_output=True, text=True)
    info["version"] = version.stdout.strip() if version.returncode == 0 else None
    config = home / ".teamai" / "config.yaml"
    info["user_scope"] = config.is_file()
    if config.is_file():
        text = config.read_text(encoding="utf-8")
        remote = re.search(r"^\s*remote:\s*(\S+)", text, re.M)
        info["repo"] = remote.group(1) if remote else None
    return info


def status(db, state, home, roots):
    # Hook はリンク越しのパス（~/.local/share 配下）で登録されるため、実体でなくリンク側と比べる。
    script = home / ".local/share" / NAME / "app" / "loop.py"
    source = Path(__file__).resolve().parent
    tools = {}
    for tool in TOOLS:
        root = tool_root(home, tool, roots.get(tool))
        config = root / ("hooks.json" if tool == "codex" else "settings.json")
        entry = {"root": str(root), "present": root.is_dir(), "hooks": []}
        if config.is_file():
            try:
                entry["hooks"] = sorted(set(owned_hooks(json.loads(config.read_text(encoding="utf-8")), script)))
            except json.JSONDecodeError:
                entry["error"] = f"設定を読めない: {config}"
        legacy = root / "retro" / "queue.md"
        if legacy.is_file():
            entry["legacy_queue"] = sum(1 for line in legacy.read_text(encoding="utf-8").splitlines()
                                        if line.startswith("- "))
        entry["skills"] = sorted(p.name for p in (root / "skills").glob("personal-*")
                                 if p.is_symlink() and p.resolve().is_relative_to(source))
        tools[tool] = entry
    personal = state / "personal.md"
    proposals = state / "proposals"
    return {
        "app": str(source), "state": str(state), "python": sys.executable,
        "tools": tools,
        "queue": {"pending": len(pending(db)),
                  "done": db.execute("SELECT COUNT(*) FROM signals WHERE done = 1").fetchone()[0]},
        "personal_md": {"exists": personal.is_file(),
                        "chars": len(personal.read_text(encoding="utf-8")) if personal.is_file() else 0,
                        "limit": PERSONAL_LIMIT},
        "proposals": len([p for p in proposals.iterdir() if p.is_file()]) if proposals.is_dir() else 0,
        "knowledge": knowledge_status(knowledge_path(state)),
        "teamai": teamai_status(home),
    }


def render_status(info):
    lines = [f"{NAME} 状態", f"  導入元: {info['app']}", f"  データ: {info['state']}"]
    for tool, entry in info["tools"].items():
        if not entry["present"]:
            lines.append(f"  {tool}: 未検出 ({entry['root']})")
            continue
        hooks = ", ".join(entry["hooks"]) if entry["hooks"] else "Hook未登録"
        skills = ", ".join(entry["skills"]) if entry["skills"] else "スキル未リンク"
        lines.append(f"  {tool}: {hooks} / {skills}")
        if entry.get("legacy_queue"):
            lines.append(f"    旧キュー {entry['legacy_queue']}件 → `migrate-legacy --tool {tool}` で取り込める")
        if entry.get("error"):
            lines.append(f"    {entry['error']}")
    queue = info["queue"]
    lines.append(f"  振り返り候補: 未処理 {queue['pending']}件 / 処理済み {queue['done']}件")
    personal = info["personal_md"]
    lines.append(f"  personal.md: {personal['chars']}文字 (上限 {personal['limit']})" if personal["exists"]
                 else "  personal.md: なし")
    lines.append(f"  共有前の草案: {info['proposals']}件")
    k = info["knowledge"]
    if not k["configured"]:
        lines.append("  knowledge: 未設定 → `knowledge init <path>` か `knowledge use <path>`")
    elif not k["exists"]:
        lines.append(f"  knowledge: {k['path']} に README.md がない")
    else:
        parts = [f"ノート {k['notes']}件"]
        if k.get("git"):
            parts.append("未コミットあり" if k.get("dirty") else "クリーン")
            if "ahead" in k:
                parts.append(f"ahead {k['ahead']} / behind {k['behind']}")
            parts.append(f"origin {k['remote']}" if k.get("remote") else "リモート未設定")
            if k.get("hooks_path") != "githooks":
                parts.append("pre-commit 未設定")
        else:
            parts.append("git 管理外")
        if not k["gitleaks"]:
            parts.append("gitleaks 未導入")
        lines.append(f"  knowledge: {k['path']} ({', '.join(parts)})")
    t = info["teamai"]
    if t["installed"]:
        scope = f"user scope: {t.get('repo')}" if t.get("user_scope") else "user scope 未接続"
        lines.append(f"  teamai: {t.get('version') or '版数不明'} ({scope})")
    else:
        lines.append("  teamai: 未導入（チーム共有は通常のPRで提案）")
    return "\n".join(lines)


def migrate_legacy(db, home, tool, root_override):
    root = tool_root(home, tool, root_override)
    queue = root / "retro" / "queue.md"
    if not queue.is_file():
        return {"queue": str(queue), "found": False}
    imported, missing, unparsed = [], [], []
    for line in queue.read_text(encoding="utf-8").splitlines():
        match = LEGACY_QUEUE.search(line)
        if not match:
            if line.strip():
                unparsed.append(line)
            continue
        session, transcript = match.groups()
        path = Path(transcript)
        if not path.is_file():
            missing.append(transcript)
            continue
        # 旧キューは作業パスを持たないため transcript 自身の記録から補い、無ければホームに寄せる。
        cwd = transcript_cwd(path) or str(home)
        imported.append({"session": session, "cwd": cwd, "signals": capture(
            db, tool, {"session_id": session, "transcript_path": transcript, "cwd": cwd})})
    return {"queue": str(queue), "found": True, "imported": imported, "missing": missing, "unparsed": unparsed}


def parse_roots(values):
    roots = {}
    for value in values or []:
        tool, _, path = value.partition("=")
        if tool not in TOOLS or not path:
            raise ValueError(f"--root は tool=PATH の形式（tool は {'/'.join(TOOLS)}）: {value}")
        roots[tool] = path
    return roots


def main():
    parser = argparse.ArgumentParser(description="個人のフィードバック候補と個人設定をローカルで管理する")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--state", type=Path,
                        help="ローカルデータの場所（既定は --home 配下の .local/state/personal-ai-loop）")
    parser.add_argument("--root", action="append", metavar="TOOL=PATH",
                        help="ツールの設定ディレクトリを明示する（既定は環境変数か ~/.<tool>）")
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook")
    hook.add_argument("event", choices=["stop", "start"])
    hook.add_argument("--tool", choices=TOOLS, required=True)
    queue = sub.add_parser("queue")
    queue.add_argument("--cwd")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("ids", type=int, nargs="+")
    ack = sub.add_parser("ack")
    ack.add_argument("ids", type=int, nargs="+")
    show = sub.add_parser("status")
    show.add_argument("--json", action="store_true")
    migrate = sub.add_parser("migrate-legacy", help="旧 retro/queue.md の候補を取り込む")
    migrate.add_argument("--tool", choices=TOOLS, required=True)
    know = sub.add_parser("knowledge", help="個人用 knowledge リポジトリ")
    know_sub = know.add_subparsers(dest="action", required=True)
    init = know_sub.add_parser("init", help="テンプレートから新規作成して登録する")
    init.add_argument("path", type=Path)
    init.add_argument("--remote", help="origin として登録する URL")
    clone = know_sub.add_parser("clone", help="既存リモートを clone して登録する")
    clone.add_argument("url")
    clone.add_argument("path", type=Path)
    use = know_sub.add_parser("use", help="既存のローカルディレクトリを登録する")
    use.add_argument("path", type=Path)
    check = know_sub.add_parser("check", help="ノート形式と索引の整合を検査する")
    check.add_argument("path", type=Path, nargs="?")
    know_sub.add_parser("unset", help="登録を外す（ファイルは消さない）")
    args = parser.parse_args()
    os.umask(0o077)
    home = args.home.expanduser().resolve()
    # --home だけ差し替えた検証で本物のデータへ触れないよう、state は home から導く。
    state = (args.state or home / ".local/state" / NAME).expanduser().absolute()
    if state.resolve().is_relative_to(Path(__file__).resolve().parent):
        raise ValueError("private state must be outside the distribution repository")
    roots = parse_roots(args.root)
    db = connect(state)
    try:
        if args.command == "hook":
            event = json.load(sys.stdin)
            if not isinstance(event, dict):
                raise ValueError("hook input must be an object")
            if args.event == "stop":
                capture(db, args.tool, event)
            else:
                start(db, state, event)
        elif args.command == "queue":
            print(json.dumps(pending(db, args.cwd), ensure_ascii=False, indent=2))
        elif args.command == "inspect":
            result = []
            for number in args.ids:
                row = db.execute("SELECT * FROM signals WHERE id = ?", (number,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown id: {number}")
                item = dict(row)
                path = Path(row["transcript"])
                match = next((s for s in signals(path) if s["fingerprint"] == row["fingerprint"]),
                             None) if path.is_file() else None
                item["evidence"] = match or {"unavailable": True}
                result.append(item)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "ack":
            with db:
                for number in args.ids:
                    if db.execute("UPDATE signals SET done = 1 WHERE id = ?", (number,)).rowcount != 1:
                        raise ValueError(f"unknown id: {number}")
            print(f"処理済み: {len(args.ids)}件")
        elif args.command == "status":
            info = status(db, state, home, roots)
            print(json.dumps(info, ensure_ascii=False, indent=2) if args.json else render_status(info))
        elif args.command == "migrate-legacy":
            result = migrate_legacy(db, home, args.tool, roots.get(args.tool))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "knowledge":
            if args.action == "unset":
                config = load_config(state)
                config.pop("knowledge", None)
                save_config(state, config)
                print("knowledge の登録を外した（ファイルは残る）")
            elif args.action == "check":
                repo = args.path or knowledge_path(state)
                if repo is None:
                    raise ValueError("knowledge が未登録。path を指定するか knowledge use で登録する")
                ok, output = knowledge.check(repo.expanduser().resolve())
                print(output or f"OK: {len(knowledge.notes(repo))}ノート、索引と整合")
                if not ok:
                    sys.exit(1)
            else:
                if args.action == "init":
                    repo = knowledge.init(args.path.expanduser().resolve(), args.remote)
                elif args.action == "clone":
                    repo = knowledge.clone(args.url, args.path.expanduser().resolve())
                else:
                    repo = knowledge.adopt(args.path.expanduser().resolve())
                config = load_config(state)
                config["knowledge"] = {"path": str(repo)}
                save_config(state, config)
                print(f"knowledge を登録: {repo}")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        # Stop をブロックする exit 2 は使わない。
        sys.exit(1)
