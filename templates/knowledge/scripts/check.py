#!/usr/bin/env python3
"""ノート形式（frontmatter・命名）と README 索引の整合を検査する。pre-commit から実行される。"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ("patterns", "decisions", "runbooks")
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.md$")

errors = []
notes = [f"{category}/{p.name}" for category in CATEGORIES if (ROOT / category).is_dir()
         for p in sorted((ROOT / category).iterdir()) if p.suffix == ".md"]

for relative in notes:
    name = relative.split("/", 1)[1]
    if not KEBAB.match(name):
        errors.append(f"{relative}: ファイル名が kebab-case でない")
    # CRLF 環境の作業ツリーでも frontmatter を読めるように正規化する
    text = (ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        errors.append(f"{relative}: frontmatter (--- ... ---) が無い")
        continue
    front = match.group(1)
    declared = re.search(r"^name:[ \t]*(\S+)[ \t]*$", front, re.M)
    if not declared:
        errors.append(f"{relative}: frontmatter に name が無い")
    elif declared.group(1) != name[:-3]:
        errors.append(f'{relative}: name "{declared.group(1)}" がファイル名と一致しない')
    if not re.search(r"^description:[ \t]*\S", front, re.M):
        errors.append(f"{relative}: frontmatter に description が無い")

index = ROOT / "README.md"
if index.is_file():
    linked = re.findall(r"\]\(((?:%s)/[^)]+\.md)\)" % "|".join(CATEGORIES), index.read_text(encoding="utf-8"))
    errors.extend(f"README.md: {p} が索引に無い" for p in notes if p not in linked)
    errors.extend(f"README.md: 索引のリンク先 {link} が存在しない" for link in linked if link not in notes)
else:
    errors.append("README.md: 無い")

if errors:
    print("ナレッジ検査 NG:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)
print(f"ナレッジ検査 OK: {len(notes)}ノート")
