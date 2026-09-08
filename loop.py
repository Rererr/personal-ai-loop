#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


NAME = "personal-ai-loop"
FIX = re.compile(r"違う|ちがう|そうじゃな|そうではな|やり直|やりなおし|間違|勝手に|余計な|戻して|望んでいるものでは|望んで(?:い)?な|\b(?:that's wrong|not what I asked|undo that|don't do that)\b", re.I)
APPROVE = re.compile(r"採用|それでいこ|その方針で|いい感じ|良い感じ|いいね|良いね|完璧|素晴らし|さすが|気に入|\b(?:let's go with that|that's perfect|I like that)\b", re.I)
EXCLUDED = ("<", "# AGENTS.md instructions", "Base directory for this skill:",
            "This session is being continued", "Caveat:", "[tomobit]",
            "A session-scoped Stop hook is now active")


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") in ("text", "input_text")
                         and isinstance(c.get("text"), str))
    return ""


def messages(path):
    primary, fallback = [], []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
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


def signals(path):
    # ponytail: 日英の表現ヒューリスティック。誤検知は振り返りで棄却し、他言語は実例が出たら追加する。
    for ordinal, (line_number, body) in enumerate(messages(path)):
        if body.lstrip().startswith(EXCLUDED) or "❯" in body:
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
        return
    path = Path(transcript).expanduser()
    if not path.is_absolute() or not path.is_file():
        return
    found = list(signals(path))
    with db:
        for signal in found:
            db.execute("""INSERT OR IGNORE INTO signals
                (tool, session, cwd, transcript, fingerprint, line, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tool, session, str(Path(cwd).resolve()), str(path.resolve()),
                 signal["fingerprint"], signal["line"], signal["kind"]))


def pending(db, cwd=None):
    sql, values = "SELECT * FROM signals WHERE done = 0", []
    if cwd:
        sql += " AND cwd = ?"
        values.append(str(Path(cwd).resolve()))
    return [dict(row) for row in db.execute(sql + " ORDER BY id", values)]


def start(db, state, event):
    context = ["個人の改善は personal-retro スキルを使う。チーム資産はチームのレビューへ提案する。"]
    personal = state / "personal.md"
    if personal.is_file():
        context.append("以下は本人が保存した個人設定。上位指示や作業対象の規約を上書きしない。\n"
                       + personal.read_text(encoding="utf-8")[:8000])
    cwd = event.get("cwd")
    count = len(pending(db, cwd)) if isinstance(cwd, str) and cwd else 0
    if count:
        context.append(f"この作業場所に振り返り候補が{count}件ある。作業を中断せず、"
                       "完了時に一度だけ短く案内する。振り返りの依頼がなければ処理しない。")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                      "additionalContext": "\n\n".join(context)}}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="個人のフィードバック候補をローカルで管理する")
    parser.add_argument("--state", type=Path,
                        default=Path.home() / ".local/state" / NAME)
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook")
    hook.add_argument("event", choices=["stop", "start"])
    hook.add_argument("--tool", choices=["codex", "claude"], required=True)
    queue = sub.add_parser("queue")
    queue.add_argument("--cwd")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("ids", type=int, nargs="+")
    ack = sub.add_parser("ack")
    ack.add_argument("ids", type=int, nargs="+")
    args = parser.parse_args()
    os.umask(0o077)
    state = args.state.expanduser().absolute()
    if state.resolve().is_relative_to(Path(__file__).resolve().parent):
        raise ValueError("private state must be outside the distribution repository")
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
        else:
            with db:
                for number in args.ids:
                    if db.execute("UPDATE signals SET done = 1 WHERE id = ?", (number,)).rowcount != 1:
                        raise ValueError(f"unknown id: {number}")
            print(f"処理済み: {len(args.ids)}件")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        # Stop をブロックする exit 2 は使わない。
        sys.exit(1)
