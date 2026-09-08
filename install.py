#!/usr/bin/env python3
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

import loop


NAME = loop.NAME
SOURCE = Path(__file__).resolve().parent
SKILLS = sorted(p.name for p in (SOURCE / "skills").iterdir() if p.is_dir())
LEGACY_SCRIPTS = ("hooks/stop-feedback-detector.sh", "hooks/sessionstart-improvement-notice.sh")
LEGACY_EXTRA = ("hooks/lib/extract-feedback.sh", "retro", "skills/retro")


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
        (root / script).resolve() for script in LEGACY_SCRIPTS}


def legacy_present(root):
    return [root / item for item in LEGACY_SCRIPTS + LEGACY_EXTRA if (root / item).exists()]


def prepare(home, root, tool, uninstall, replace_legacy):
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
            handler = {"type": "command", "command": command, "timeout": 10}
            group = {"hooks": [handler]}
            if event == "SessionStart":
                group["matcher"] = "startup|resume|clear|compact"
                if tool == "codex":
                    # Codex は既定で約2,500トークン超の additionalContext を別ファイルへ退避する。
                    handler["additionalContextLimit"] = 6000
            hooks.setdefault(event, []).append(group)
    if not hooks:
        del value["hooks"]
    links = []
    for skill in SKILLS:
        link = root / "skills" / skill
        target = app / "skills" / skill
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or Path(os.readlink(link)) != target:
                raise ValueError(f"既存の同名スキルを上書きしません: {link}")
        links.append((link, target))
    # 整形の違いだけで書き換えないよう、比較は文字列でなく構造で行う。
    changed = value != json.loads(before)
    return {"tool": tool, "root": root, "config": config, "before": before, "changed": changed,
            "after": json.dumps(value, ensure_ascii=False, indent=2) + "\n" if changed else before,
            "links": links, "removed": removed}


def launcher(home):
    return home / ".local/bin" / "pal"


def retire_legacy(home, plan, stamp, db):
    tool, root = plan["tool"], plan["root"]
    imported = loop.migrate_legacy(db, home, tool, root)
    archive = home / ".local/state" / NAME / "backups" / stamp / "legacy" / tool
    moved = []
    for item in legacy_present(root):
        destination = archive / item.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item), str(destination))
        moved.append(str(item))
    return {"imported": imported, "archived": moved, "archive": str(archive)}


def ask(prompt, default=""):
    shown = f" [{default}]" if default else ""
    answer = input(f"{prompt}{shown}: ").strip()
    return answer or default


def yes(prompt, default=True):
    answer = ask(prompt + (" [Y/n]" if default else " [y/N]")).lower()
    return default if not answer else answer in ("y", "yes")


def wizard(args, home, roots):
    print(f"{NAME} セットアップ（変更前に予定を表示し、確認してから適用します）\n")
    detected = [tool for tool in loop.TOOLS if loop.tool_root(home, tool, roots.get(tool)).is_dir()]
    print("[1/3] 導入するAIツール")
    for tool in loop.TOOLS:
        mark = "検出" if tool in detected else "未検出"
        print(f"  {tool}: {mark} ({loop.tool_root(home, tool, roots.get(tool))})")
    if not detected:
        raise ValueError("導入先のツールが見つかりません。--root tool=PATH で設定ディレクトリを指定してください")
    chosen = ask("  導入するツール（カンマ区切り）", ",".join(detected))
    args.tool = [t.strip() for t in chosen.split(",") if t.strip()]
    unknown = [t for t in args.tool if t not in loop.TOOLS]
    if unknown:
        raise ValueError(f"未対応のツール: {', '.join(unknown)}")
    print("\n[2/3] 旧フィードバック機構")
    found = [item for tool in args.tool for item in legacy_present(loop.tool_root(home, tool, roots.get(tool)))]
    if found:
        for item in found:
            print(f"  検出: {item}")
        args.replace_legacy = yes("  新しいHookへ置き換えますか")
        args.retire_legacy = args.replace_legacy and yes("  旧キューを取り込み、旧ファイルをバックアップへ退避しますか")
    else:
        print("  なし")
    print("\n[3/3] 個人用 knowledge リポジトリ（プロジェクト横断の知見を置く本人専用の git リポジトリ）")
    current = loop.knowledge_path(home / ".local/state" / NAME)
    print(f"  現在: {current if current else '未設定'}")
    print("  1) 既にローカルにある  2) リモートにある（clone する）  3) 新規作成  4) 今は設定しない")
    choice = ask("  選択", "4" if current else "3")
    if choice == "1":
        args.knowledge_use = Path(ask("  パス"))
    elif choice == "2":
        url = ask("  リポジトリURL")
        args.knowledge_clone = [url, Path(ask("  clone 先", str(home / "knowledge")))]
    elif choice == "3":
        args.knowledge_init = Path(ask("  作成先", str(home / "knowledge")))
        args.knowledge_remote = ask("  origin のURL（空なら後で設定）") or None
    print()


def main():
    parser = argparse.ArgumentParser(description="個人用Hookとスキルだけを登録する。引数なしで対話セットアップ")
    parser.add_argument("--tool", choices=loop.TOOLS, action="append")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--root", action="append", metavar="TOOL=PATH",
                        help="ツールの設定ディレクトリを明示する（既定は環境変数か ~/.<tool>）")
    parser.add_argument("--apply", action="store_true", help="省略時は変更予定の表示のみ")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--replace-legacy", action="store_true", help="旧feedback Hookの登録を置き換える")
    parser.add_argument("--retire-legacy", action="store_true",
                        help="旧キューを取り込み、旧スクリプト・キュー・スキルをバックアップへ退避する")
    parser.add_argument("--knowledge-use", type=Path, metavar="PATH", help="既存の knowledge を登録")
    parser.add_argument("--knowledge-clone", nargs=2, metavar=("URL", "PATH"), help="knowledge を clone して登録")
    parser.add_argument("--knowledge-init", type=Path, metavar="PATH", help="knowledge を新規作成して登録")
    parser.add_argument("--knowledge-remote", metavar="URL", help="--knowledge-init で origin にするURL")
    parser.add_argument("--interactive", action="store_true", help="端末でなくても対話セットアップを行う（検証用）")
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    if home.is_relative_to(SOURCE):
        raise ValueError("導入先は配布リポジトリの外にしてください")
    roots = loop.parse_roots(args.root)
    interactive = not args.tool and not args.uninstall and (args.interactive or sys.stdin.isatty())
    if interactive:
        wizard(args, home, roots)
    if not args.tool:
        raise ValueError("--tool を指定するか、端末から引数なしで対話セットアップを実行してください")
    if args.retire_legacy and (args.uninstall or not args.replace_legacy):
        raise ValueError("--retire-legacy は --replace-legacy と併用し、--uninstall とは併用できません")
    app = home / ".local/share" / NAME / "app"
    if app.exists() or app.is_symlink():
        if not app.is_symlink() or app.resolve() != SOURCE:
            raise ValueError(f"別の導入元が登録されています: {app}")
    plans = [prepare(home, loop.tool_root(home, tool, roots.get(tool)), tool, args.uninstall, args.replace_legacy)
             for tool in dict.fromkeys(args.tool)]
    knowledge_action = next((label for label, value in (
        ("use", args.knowledge_use), ("clone", args.knowledge_clone), ("init", args.knowledge_init)) if value), None)
    for plan in plans:
        print(json.dumps({"tool": plan["tool"], "config": str(plan["config"]),
                          "changed": plan["changed"],
                          "skills": [str(link) for link, _ in plan["links"]], "remove": plan["removed"],
                          "legacy": [str(p) for p in legacy_present(plan["root"])] if args.retire_legacy else [],
                          "action": "uninstall" if args.uninstall else "install"}, ensure_ascii=False))
    print(json.dumps({"launcher": str(launcher(home)), "knowledge": knowledge_action}, ensure_ascii=False))
    if not args.apply and not (interactive and yes("適用しますか", False)):
        print("dry-run: 変更なし。適用する場合は --apply を追加してください")
        return
    os.umask(0o077)
    # 計画後の別プロセスによる変更を、古い設定で上書きしない。
    for plan in plans:
        config = plan["config"]
        current = config.read_text(encoding="utf-8") if config.exists() else "{}\n"
        if current != plan["before"]:
            raise ValueError(f"設定が変更されました。再実行してください: {config}")
    if not args.uninstall:
        app.parent.mkdir(parents=True, exist_ok=True)
        if not app.is_symlink():
            app.symlink_to(SOURCE, target_is_directory=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    state = home / ".local/state" / NAME
    for plan in plans:
        config = plan["config"]
        if plan["changed"]:
            if config.exists():
                backup = state / "backups" / stamp / plan["root"].name / config.name
                atomic_write(backup, plan["before"])
                print(f"backup: {backup}")
            atomic_write(config, plan["after"])
        for link, target in plan["links"]:
            if args.uninstall:
                if link.is_symlink():
                    link.unlink()
            else:
                link.parent.mkdir(parents=True, exist_ok=True)
                if not link.is_symlink():
                    link.symlink_to(target, target_is_directory=True)
    bin_link = launcher(home)
    if args.uninstall:
        if bin_link.is_symlink() and bin_link.resolve() == SOURCE / "pal":
            bin_link.unlink()
    else:
        bin_link.parent.mkdir(parents=True, exist_ok=True)
        if bin_link.is_symlink() or not bin_link.exists():
            if bin_link.is_symlink():
                bin_link.unlink()
            bin_link.symlink_to(SOURCE / "pal")
        else:
            print(f"注意: {bin_link} が既にあるためコマンド `pal` は登録しなかった")
    if args.retire_legacy or knowledge_action:
        db = loop.connect(state)
        try:
            if args.retire_legacy:
                for plan in plans:
                    print(json.dumps({"retired": plan["tool"], **retire_legacy(home, plan, stamp, db)},
                                     ensure_ascii=False))
            if knowledge_action:
                if knowledge_action == "use":
                    repo = loop.knowledge.adopt(args.knowledge_use.expanduser().resolve())
                elif knowledge_action == "clone":
                    url, path = args.knowledge_clone
                    repo = loop.knowledge.clone(url, Path(path).expanduser().resolve())
                else:
                    repo = loop.knowledge.init(args.knowledge_init.expanduser().resolve(), args.knowledge_remote)
                config = loop.load_config(state)
                config["knowledge"] = {"path": str(repo)}
                loop.save_config(state, config)
                print(f"knowledge を登録: {repo}")
        finally:
            db.close()
    print("完了。学習データ・既存の改善キューは保持しています。")
    if not args.uninstall:
        on_path = str(bin_link.parent) in os.environ.get("PATH", "").split(os.pathsep)
        print("状態確認: pal status" if on_path else f"状態確認: {bin_link} status（{bin_link.parent} を PATH に加えると `pal` で呼べる）")
        print("AIツールを再起動してください。Codexでは /hooks から新しいHookを確認・信頼してください。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, loop.subprocess.CalledProcessError) as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print(f"\n{NAME}: 中断。適用途中なら backups/ を確認してください", file=sys.stderr)
        sys.exit(1)
