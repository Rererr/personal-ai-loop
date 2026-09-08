"""個人用 knowledge リポジトリのテンプレートと検査。ツール非依存の Markdown リポジトリを扱う。"""
from pathlib import Path
import subprocess

CATEGORIES = ("patterns", "decisions", "runbooks")
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "knowledge"


def run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def notes(repo):
    found = []
    for category in CATEGORIES:
        directory = repo / category
        if directory.is_dir():
            found.extend(f"{category}/{p.name}" for p in sorted(directory.iterdir()) if p.suffix == ".md")
    return found


def checker(repo):
    for candidate, runner in (("scripts/check.py", ["python3"]), ("scripts/check.mjs", ["node"])):
        if (repo / candidate).is_file():
            return runner + [str(repo / candidate)]
    return None


def check(repo):
    # 検査規則の正本はリポジトリ側の scripts/ に置く（他の機械・ツールからも同じ検査が走るように）。
    command = checker(repo)
    if command is None:
        return False, "検査スクリプト（scripts/check.py か scripts/check.mjs）が無い"
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def render(target):
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"空でないディレクトリには作成しない: {target}")
    for source in sorted(TEMPLATE_DIR.rglob("*")):
        if source.is_dir():
            continue
        destination = target / source.relative_to(TEMPLATE_DIR)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        if source.parent.name in ("githooks", "scripts"):
            destination.chmod(0o755)
    for category in CATEGORIES:
        (target / category).mkdir(exist_ok=True)
        (target / category / ".gitkeep").touch()


def init(target, remote=None):
    render(target)
    run(target, "init", "-q", "-b", "main")
    run(target, "config", "core.hooksPath", "githooks")
    if remote:
        run(target, "remote", "add", "origin", remote)
    run(target, "add", "-A")
    try:
        run(target, "commit", "-q", "-m", "chore: knowledge リポジトリを初期化")
    except subprocess.CalledProcessError as error:
        # 署名者情報が無い環境では初回コミットだけ本人に任せる。
        raise ValueError("初期コミットに失敗（git の user.name / user.email を設定してから "
                         f"`git -C {target} commit` を実行）: {error.stderr.strip()}") from error
    return target


def clone(url, target):
    if target.exists():
        raise ValueError(f"既に存在する: {target}")
    subprocess.run(["git", "clone", "-q", url, str(target)], check=True, capture_output=True, text=True)
    return adopt(target)


def adopt(target):
    if not (target / "README.md").is_file():
        raise ValueError(f"README.md が無いので knowledge として登録しない: {target}")
    if (target / "githooks").is_dir() and run(target, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true":
        run(target, "config", "core.hooksPath", "githooks")
    return target
