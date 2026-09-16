#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from loop import signals, tool_root, TOOL_ENV


ROOT = Path(__file__).resolve().parent


def isolate_tool_env():
    # 設定ディレクトリの切替変数は --home の仮ホームより優先されるため、引き継ぐと実環境を検査してしまう。
    # 個々の subprocess に env を渡すのでなく、テストプロセス自身から落として全経路に効かせる。
    return sorted(name for name in TOOL_ENV.values() if os.environ.pop(name, None) is not None)


isolate_tool_env()


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


def extended():
    with tempfile.TemporaryDirectory(prefix="personal-loop-test-") as temporary:
        base = Path(temporary).resolve()  # 導入先は解決済みパスで登録されるため比較も解決後で行う
        home = base / "home"
        claude = home / "cfg" / "claude-work"  # 既定と違う設定ディレクトリを --root で指す
        codex = home / ".codex"
        for directory in (claude / "hooks" / "lib", claude / "retro", claude / "skills" / "retro", codex):
            directory.mkdir(parents=True)
        (claude / "hooks/stop-feedback-detector.sh").write_text("#!/bin/bash\n")
        (claude / "hooks/lib/extract-feedback.sh").write_text("x")
        (claude / "skills/retro/SKILL.md").write_text("x")
        old_log = base / "old.jsonl"
        write_rows(old_log, [{"type": "user", "cwd": str(base / "proj"), "message": {"content": "最初の依頼"}},
                             user("違う、そうじゃない。やり直して")])
        (claude / "retro/queue.md").write_text(
            f"- 2026-09-07 16:30 session=s1 修正指示1件 承認0件 transcript={old_log}\n"
            f"- 2026-09-01 10:00 session=s0 修正指示1件 承認0件 transcript={base}/gone.jsonl\n"
            "- 2026-08-01 形式の合わない行\n", encoding="utf-8")
        (claude / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": shlex.join(["bash", str(claude / "hooks/stop-feedback-detector.sh")])}]}]}}))
        state = home / ".local/state/personal-ai-loop"
        cli = ("--home", home, "--root", f"claude={claude}", "--state", state)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

        home_only = json.loads(run("loop.py", "--home", home, "status", "--json"))
        assert home_only["state"] == str(home / ".local/state/personal-ai-loop")
        assert (home / ".local/state/personal-ai-loop/queue.sqlite3").is_file()
        before = json.loads(run("loop.py", *cli, "status", "--json"))
        assert before["tools"]["claude"]["legacy_queue"] == 3 and before["tools"]["claude"]["hooks"] == []
        assert "旧キュー 3件" in run("loop.py", *cli, "status") and "knowledge: 未設定" in run("loop.py", *cli, "status")

        # 対話セットアップ: 両ツール、旧機構の置換と退避、knowledge 新規作成、適用
        answers = f"claude,codex\ny\ny\n3\n{home / 'knowledge'}\ngit@example.com:me/knowledge.git\ny\n"
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--home", str(home),
                                 "--root", f"claude={claude}", "--interactive"],
                                input=answers, capture_output=True, text=True, env=env)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "knowledge を登録" in result.stdout and '"missing": ["' in result.stdout
        assert '"unparsed": ["- 2026-08-01 形式の合わない行"]' in result.stdout
        settings = json.loads((claude / "settings.json").read_text())
        assert [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]] == [shlex.join(
            [sys.executable, str(home / ".local/share/personal-ai-loop/app/loop.py"), "--state", str(state),
             "hook", "stop", "--tool", "claude"])]
        codex_start = json.loads((codex / "hooks.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]
        assert codex_start["additionalContextLimit"] == 6000
        assert "additionalContextLimit" not in json.dumps(settings)
        assert not (claude / "retro").exists() and not (claude / "skills/retro").exists()
        assert not (claude / "hooks/lib/extract-feedback.sh").exists()
        archived = list(state.glob("backups/*/legacy/claude/retro/queue.md"))
        assert len(archived) == 1 and "session=s1" in archived[0].read_text(encoding="utf-8")
        assert (home / ".local/bin/pal").is_symlink()
        for tool_root in (claude, codex):
            assert (tool_root / "skills/personal-retro/SKILL.md").is_file()
            assert (tool_root / "skills/personal-setup/SKILL.md").is_file()

        after = json.loads(run("loop.py", *cli, "status", "--json"))
        assert after["tools"]["claude"]["hooks"] == ["SessionStart", "Stop"]
        assert "legacy_queue" not in after["tools"]["claude"]
        assert after["queue"]["pending"] == 1 and after["knowledge"]["notes"] == 0
        assert after["knowledge"]["remote"] == "git@example.com:me/knowledge.git"
        queue = json.loads(run("loop.py", *cli, "queue", "--cwd", base / "proj"))
        assert len(queue) == 1 and queue[0]["session"] == "s1" and queue[0]["line"] == 2

        knowledge = home / "knowledge"
        assert subprocess.run(["git", "-C", str(knowledge), "config", "core.hooksPath"],
                              capture_output=True, text=True).stdout.strip() == "githooks"
        assert (knowledge / "CLAUDE.md").read_text() == "@AGENTS.md\n"
        context = json.loads(run("loop.py", *cli, "hook", "start", "--tool", "claude",
                                 data={"cwd": str(base / "proj"), "source": "startup"}))
        text = context["hookSpecificOutput"]["additionalContext"]
        assert str(knowledge) in text and "1件" in text

        # 索引整合の検査: 名前不一致・索引漏れを検出し、直せば通る
        bad = knowledge / "patterns" / "Bad_Name.md"
        bad.write_text("---\nname: other\n---\n本文\n", encoding="utf-8")
        failure = subprocess.run([sys.executable, str(ROOT / "loop.py"), *map(str, cli), "knowledge", "check"],
                                 capture_output=True, text=True)
        assert failure.returncode == 1
        for expected in ("kebab-case", "一致しない", "description", "索引に無い"):
            assert expected in failure.stdout, failure.stdout
        bad.unlink()
        good = knowledge / "patterns" / "measure-first.md"
        good.write_text("---\nname: measure-first\ndescription: 推測でなく計測する\n---\n本文\n", encoding="utf-8")
        with (knowledge / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("- [計測](patterns/measure-first.md) — 推測しない\n")
        assert "OK" in run("loop.py", *cli, "knowledge", "check")
        pre_commit = subprocess.run(["git", "-C", str(knowledge), "commit", "-qam", "note"],
                                    capture_output=True, text=True, env=env)
        assert pre_commit.returncode == 0, pre_commit.stderr
        assert json.loads(run("loop.py", *cli, "status", "--json"))["knowledge"]["notes"] == 1

        # 既存の登録替え: README の無い場所は拒否、clone は登録まで行う
        run("loop.py", *cli, "knowledge", "use", base / "proj", ok=False)
        clone_to = home / "knowledge-clone"
        run("loop.py", *cli, "knowledge", "clone", knowledge, clone_to)
        assert json.loads(run("loop.py", *cli, "status", "--json"))["knowledge"]["path"] == str(clone_to)
        run("loop.py", *cli, "knowledge", "unset")
        assert not json.loads(run("loop.py", *cli, "status", "--json"))["knowledge"]["configured"]

        # 再導入は冪等、削除は launcher とスキルリンクを外し knowledge は残す
        run("install.py", "--home", home, "--root", f"claude={claude}", "--tool", "claude", "--tool", "codex", "--apply")
        run("install.py", "--home", home, "--root", f"claude={claude}", "--tool", "claude", "--tool", "codex",
            "--uninstall", "--apply")
        assert not (home / ".local/bin/pal").exists() and not (codex / "skills/personal-setup").exists()
        assert "hooks" not in json.loads((claude / "settings.json").read_text())
        assert (clone_to / "README.md").is_file() and (state / "queue.sqlite3").is_file()
        run("install.py", "--home", home, "--retire-legacy", "--tool", "claude", ok=False)
        run("install.py", "--home", home, "--retire-legacy", "--replace-legacy", "--uninstall", "--tool", "claude", ok=False)
        # 整形だけが違う設定は、削除でも導入でも書き換えない
        pretty = json.dumps({"permissions": {"allow": []}}, indent=4) + "\n\n"
        (claude / "settings.json").write_text(pretty)
        run("install.py", "--home", home, "--root", f"claude={claude}", "--tool", "claude", "--uninstall", "--apply")
        assert (claude / "settings.json").read_text() == pretty
        assert '"changed": false' in run("install.py", "--home", home, "--root", f"claude={claude}", "--tool", "claude", "--uninstall")
        # 受け入れない clone 先は残さない
        bare = base / "noreadme"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", str(bare)], check=True)
        (bare / "x.txt").write_text("x")
        subprocess.run(["git", "-C", str(bare), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(bare), "commit", "-qm", "x"], check=True, env=env)
        run("loop.py", *cli, "knowledge", "clone", bare, home / "rejected", ok=False)
        assert not (home / "rejected").exists()
    print("PASS: 対話セットアップ・--root・旧機構退避と取り込み・status・knowledge 作成/検査/clone/登録替え・pal")


def main():
    extended()
    with tempfile.TemporaryDirectory(prefix="personal-loop-test-") as temporary:
        base = Path(temporary)
        home = base / "home ' with $ and ` chars"
        # 両変数が未設定の CI でも撤去を検出できるよう、障害条件をテスト自身で作ってから密閉を確かめる。
        for name in TOOL_ENV.values():
            os.environ[name] = str(base / "must-not-be-used")
        assert tool_root(home, "claude") == base / "must-not-be-used"  # 本番は環境変数が --home より強い
        assert isolate_tool_env() == sorted(TOOL_ENV.values())
        assert tool_root(home, "claude") == home / ".claude" and tool_root(home, "codex") == home / ".codex"
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
        rows = [user("引き継ぎです。勝手に rebase・push をしない方針で進めてください"),
                user("APIとは違う。間違いない。"), user("<system-reminder>やり直して</system-reminder>"),
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
        assert evidence[0]["evidence"]["text"] == rows[5]["message"]["content"]
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
            {"type": "event_msg", "payload": {"type": "user_message", "message": "最初の依頼"}},
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
                    "content": [{"type": "input_text", "text": "first task"}]}},
                               {"type": "response_item", "payload": {"type": "message", "role": "user",
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
