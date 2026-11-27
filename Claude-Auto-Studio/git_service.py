"""Project file and Git helpers."""
from __future__ import annotations

import subprocess
from pathlib import Path


IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}


def list_project_files(project: Path, limit: int = 1500) -> list[str]:
    files: list[str] = []
    try:
        for path in sorted(project.rglob("*")):
            if any(part in IGNORED_DIRS for part in path.relative_to(project).parts):
                continue
            if path.is_file():
                files.append(str(path.relative_to(project)))
                if len(files) >= limit:
                    break
    except OSError:
        pass
    return files


def get_git_diff(project: Path) -> str:
    try:
        diff = subprocess.run(
            ["git", "diff", "--", "."],
            cwd=str(project),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(project),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if diff.returncode == 0 and status.returncode == 0:
            content = diff.stdout.strip()
            untracked = [line for line in status.stdout.splitlines() if "??" in line]
            if untracked:
                content += ("\n\n" if content else "") + "未追跡ファイル:\n" + "\n".join(untracked)
            return content or "変更はありません"
        return "Gitリポジトリではないか、gitを実行できません。"
    except Exception as exc:
        return f"差分を取得できません: {exc}"
