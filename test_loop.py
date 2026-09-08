#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from loop import signals


ROOT = Path(__file__).resolve().parent


def run(script, *args, data=None, ok=True):
    result = subprocess.run([sys.executable, str(ROOT / script), *map(str, args)],
                            input=json.dumps(data) if data is not None else None,
                            capture_output=True, text=True)
    assert (result.returncode == 0) == ok, (result.args, result.stdout, result.stderr)
    return result.stdout


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def user(text):
    return {"type": "user", "message": {"content": text}}


def main():
    with tempfile.TemporaryDirectory(prefix="personal-loop-test-") as temporary:
        base = Path(temporary)
        home = base / "home ' with $ and ` chars"
        state = home / ".local/state/personal-ai-loop"
        codex = home / ".codex/hooks.json"
        claude = home / ".claude/settings.json"
        for config in (codex, claude):
            config.parent.mkdir(parents=True)
        unrelated = {"type": "command", "command": "printf retained"}
        old = {"type": "command", "command": shlex.join([
            "bash", str(home / ".codex/hooks/stop-feedback-detector.sh")])}
        original = {"untouched": {"enabled": True}, "hooks": {
            "Stop": [{"matcher": "", "hooks": [unrelated, old]}]}}
        codex.write_text(json.dumps(original), encoding="utf-8")
        claude.write_text('{"permissions":{"allow":[]}}', encoding="utf-8")
        install_args = ("--home", home, "--tool", "codex", "--tool", "claude")
        before = codex.read_bytes()
        run("install.py", *install_args, ok=False)
        run("install.py", *install_args, "--replace-legacy")
        assert codex.read_bytes() == before and not state.exists()
        run("install.py", *install_args, "--replace-legacy", "--apply")
        after = json.loads(codex.read_text())
        assert after["untouched"] == original["untouched"]
        assert after["hooks"]["Stop"][0]["hooks"] == [unrelated]
        assert any(p.read_bytes() == before for p in state.glob("backups/*/.codex/hooks.json"))
        installed = codex.read_bytes()
        run("install.py", *install_args, "--apply")
        assert codex.read_bytes() == installed
        assert (home / ".claude/skills/personal-retro/SKILL.md").is_file()

        transcript = base / "claude.jsonl"
        rows = [user("APIとは違う。間違いない。"), user("<system-reminder>やり直して</system-reminder>"),
                user("# AGENTS.md instructions\n勝手に書かない"),
                {"type": "assistant", "message": {"content": "やり直します"}},
                user("そうじゃない。勝手に変更しないで"),
                user([{"type": "tool_result", "content": "間違っている"},
                      {"type": "text", "text": "その方針で。いい感じ"}])]
        write_rows(transcript, rows)
        event = {"session_id": "session-a", "cwd": str(base / "project-a"), "transcript_path": str(transcript)}
        cli = ("--state", state)
        stop_args = (*cli, "hook", "stop", "--tool", "claude")
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: run("loop.py", *stop_args, data=event), range(3)))
        pending = json.loads(run("loop.py", *cli, "queue"))
        assert [s["kind"] for s in pending] == ["fix", "approve"]
        assert "そうじゃない" not in (state / "queue.sqlite3").read_bytes().decode("utf-8", errors="ignore")
        evidence = json.loads(run("loop.py", *cli, "inspect", pending[0]["id"]))
        assert evidence[0]["evidence"]["text"] == rows[4]["message"]["content"]
        old_ids = [s["id"] for s in pending]
        rows.append(user("余計な処理がまだある。戻して"))
        write_rows(transcript, rows)
        run("loop.py", *stop_args, data=event)
        run("loop.py", *cli, "ack", *old_ids)
        run("loop.py", *stop_args, data=event)
        remaining = json.loads(run("loop.py", *cli, "queue"))
        assert len(remaining) == 1 and remaining[0]["id"] not in old_ids
        run("loop.py", *cli, "ack", remaining[0]["id"], 999999, ok=False)
        assert len(json.loads(run("loop.py", *cli, "queue"))) == 1
        assert json.loads(run("loop.py", *cli, "queue", "--cwd", base / "other")) == []
        (state / "personal.md").write_text("回答は簡潔に。", encoding="utf-8")
        for tool, config in (("codex", codex), ("claude", claude)):
            command = json.loads(config.read_text())["hooks"]["SessionStart"][-1]["hooks"][0]["command"]
            result = subprocess.run(command, shell=True, input=json.dumps(event), text=True, capture_output=True)
            assert result.returncode == 0, result.stderr
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            assert "回答は簡潔に。" in context and "1件" in context

        codex_log = base / "codex.jsonl"
        msg = "やり直して"
        write_rows(codex_log, [
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": msg}]}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": msg}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": msg}},
        ])
        assert len(list(signals(codex_log))) == 1
        event["transcript_path"] = str(codex_log)
        run("loop.py", *cli, "hook", "stop", "--tool", "codex", data=event)
        assert len(json.loads(run("loop.py", *cli, "queue"))) == 2
        write_rows(codex_log, [{"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "That's wrong. Undo that."}]}}])
        assert len(list(signals(codex_log))) == 1
        with codex_log.open("a") as stream:
            stream.write('{"incomplete":')
        assert len(list(signals(codex_log))) == 1
        transcript.unlink()
        evidence = json.loads(run("loop.py", *cli, "inspect", remaining[0]["id"]))
        assert evidence[0]["evidence"]["unavailable"]
        run("loop.py", *cli, "hook", "stop", "--tool", "codex", data={})
        run("loop.py", *cli, "hook", "stop", "--tool", "codex", data=[], ok=False)

        personal_before = (state / "personal.md").read_bytes()
        run("install.py", *install_args, "--uninstall", "--apply")
        uninstalled = json.loads(codex.read_text())
        assert uninstalled == {"untouched": original["untouched"],
                               "hooks": {"Stop": [{"matcher": "", "hooks": [unrelated]}]}}
        assert (state / "personal.md").read_bytes() == personal_before
        assert not (home / ".codex/skills/personal-retro").exists()
        assert (state / "queue.sqlite3").exists()
        run("install.py", *install_args, "--uninstall", "--apply")
        conflict = home / ".claude/skills/personal-retro"
        conflict.mkdir()
        saved = codex.read_bytes()
        run("install.py", *install_args, "--apply", ok=False)
        assert codex.read_bytes() == saved
        conflict.rmdir()
        claude.write_text("not json", encoding="utf-8")
        run("install.py", *install_args, "--apply", ok=False)
        assert codex.read_bytes() == saved
        assert os.stat(state / "queue.sqlite3").st_mode & 0o777 == 0o600
    print("PASS: 導入・更新・削除・既存設定保持・移行・両ログ形式・並行検出・処理済み管理・個人データ保持")


if __name__ == "__main__":
    main()
