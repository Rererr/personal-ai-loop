#!/usr/bin/env python3
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile


NAME = "personal-ai-loop"
SKILL = "personal-retro"
SOURCE = Path(__file__).resolve().parent


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def legacy(command, root):
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return len(words) == 2 and words[0] == "bash" and Path(words[1]).expanduser().resolve() in {
        (root / "hooks/stop-feedback-detector.sh").resolve(),
        (root / "hooks/sessionstart-improvement-notice.sh").resolve()}


def prepare(home, tool, uninstall, replace_legacy):
    root = home / f".{tool}"
    config = root / ("hooks.json" if tool == "codex" else "settings.json")
    if config.is_symlink():
        raise ValueError(f"設定ファイルのsymlinkは自動編集しません: {config}")
    before = config.read_text(encoding="utf-8") if config.exists() else "{}\n"
    value = json.loads(before)
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise ValueError(f"不正なhooks設定: {config}")
    value = deepcopy(value)
    hooks = value.setdefault("hooks", {})
    app = home / ".local/share" / NAME / "app"
    script = app / "loop.py"
    state = home / ".local/state" / NAME
    removed = []
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            raise ValueError(f"不正なhook group: {config}: {event}")
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"不正なhook group: {config}: {event}")
            kept = []
            for handler in group["hooks"]:
                if not isinstance(handler, dict):
                    raise ValueError(f"不正なhandler: {config}")
                command = handler.get("command", "")
                if not isinstance(command, str):
                    raise ValueError(f"不正なcommand: {config}")
                try:
                    words = shlex.split(command)
                except ValueError:
                    words = []
                owned = len(words) > 1 and words[1] == str(script)
                old = legacy(command, root)
                if old and not uninstall and not replace_legacy:
                    raise ValueError("旧feedback Hookがあります。移行する場合は --replace-legacy を指定してください")
                if owned or (old and replace_legacy and not uninstall):
                    removed.append(command)
                else:
                    kept.append(handler)
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not uninstall:
        for event, action in (("SessionStart", "start"), ("Stop", "stop")):
            command = shlex.join([sys.executable, str(script), "--state", str(state),
                                  "hook", action, "--tool", tool])
            group = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
            if event == "SessionStart":
                group["matcher"] = "startup|resume|clear|compact"
            hooks.setdefault(event, []).append(group)
    link = root / "skills" / SKILL
    target = app / "skills" / SKILL
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or Path(os.readlink(link)) != target:
            raise ValueError(f"既存の同名スキルを上書きしません: {link}")
    return config, before, json.dumps(value, ensure_ascii=False, indent=2) + "\n", link, target, removed


def main():
    parser = argparse.ArgumentParser(description="個人用Hookとスキルだけを登録する")
    parser.add_argument("--tool", choices=["codex", "claude"], action="append", required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--apply", action="store_true", help="省略時は変更予定の表示のみ")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--replace-legacy", action="store_true")
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    if home.is_relative_to(SOURCE):
        raise ValueError("導入先は配布リポジトリの外にしてください")
    app = home / ".local/share" / NAME / "app"
    if app.exists() or app.is_symlink():
        if not app.is_symlink() or app.resolve() != SOURCE:
            raise ValueError(f"別の導入元が登録されています: {app}")
    plans = [prepare(home, tool, args.uninstall, args.replace_legacy)
             for tool in dict.fromkeys(args.tool)]
    for config, before, after, link, target, removed in plans:
        print(json.dumps({"config": str(config), "changed": before != after,
                          "skill": str(link), "remove": removed,
                          "action": "uninstall" if args.uninstall else "install"}, ensure_ascii=False))
    if not args.apply:
        print("dry-run: 変更なし。適用する場合は --apply を追加してください")
        return
    os.umask(0o077)
    # 計画後の別プロセスによる変更を、古い設定で上書きしない。
    for config, before, *_ in plans:
        current = config.read_text(encoding="utf-8") if config.exists() else "{}\n"
        if current != before:
            raise ValueError(f"設定が変更されました。再実行してください: {config}")
    if not args.uninstall:
        app.parent.mkdir(parents=True, exist_ok=True)
        if not app.is_symlink():
            app.symlink_to(SOURCE, target_is_directory=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for config, before, after, link, target, _ in plans:
        if before != after:
            if config.exists():
                backup = home / ".local/state" / NAME / "backups" / stamp / config.parent.name / config.name
                atomic_write(backup, before)
                print(f"backup: {backup}")
            atomic_write(config, after)
        if args.uninstall:
            if link.is_symlink():
                link.unlink()
        else:
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.is_symlink():
                link.symlink_to(target, target_is_directory=True)
    print("完了。学習データ・既存の改善キューは保持しています。")
    if not args.uninstall:
        print("AIツールを再起動してください。Codexでは /hooks から新しいHookを確認・信頼してください。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        sys.exit(1)
