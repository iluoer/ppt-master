"""Project and artifact helpers for the hosted PPT Master app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_ROOT = Path(os.environ.get("PPT_MASTER_RUNS_DIR", "/tmp/ppt-master-hosted"))


@dataclass
class ProjectInfo:
    project_name: str
    project_path: Path
    created_at: str


def safe_slug(value: str, fallback: str = "deck") -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())[:48].strip("-_")
    return slug or fallback


def run_root() -> Path:
    root = Path(os.environ.get("PPT_MASTER_RUNS_DIR", str(DEFAULT_RUN_ROOT)))
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_parent() -> Path:
    parent = run_root() / "projects"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def create_project(topic: str, *, fmt: str = "ppt169") -> ProjectInfo:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    project_name = f"hosted_{stamp}_{safe_slug(topic)}"
    cmd = [
        sys.executable,
        "skills/ppt-master/scripts/project_manager.py",
        "init",
        project_name,
        "--format",
        fmt,
        "--dir",
        str(project_parent()),
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, text=True, capture_output=True)
    project_path = project_parent() / project_name
    created_at = datetime.now(timezone.utc).isoformat()
    (project_path / "hosted_session.json").write_text(
        json.dumps(
            {
                "project_name": project_name,
                "created_at": created_at,
                "topic": topic,
                "format": fmt,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ProjectInfo(project_name=project_name, project_path=project_path, created_at=created_at)


def write_user_prompt(project_path: Path, prompt: str) -> Path:
    sources = project_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    target = sources / "user_prompt.md"
    target.write_text(prompt.strip() + "\n", encoding="utf-8")
    return target


def import_uploads(project_path: Path, files: Iterable[str] | None) -> list[Path]:
    if not files:
        return []
    uploads_dir = project_path / "sources" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for item in files:
        source = Path(item)
        if not source.exists() or not source.is_file():
            continue
        target = uploads_dir / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def append_chat(project_path: Path, role: str, content: str) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "content": content,
    }
    with (project_path / "chat.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def latest_pptx(project_path: Path | None) -> str | None:
    if not project_path:
        return None
    exports = project_path / "exports"
    if not exports.exists():
        return None
    files = sorted(exports.glob("*.pptx"), key=lambda path: path.stat().st_mtime, reverse=True)
    return str(files[0]) if files else None


def slide_paths(project_path: Path | None) -> list[Path]:
    if not project_path:
        return []
    candidates = [project_path / "svg_output", project_path / "svg_final"]
    for folder in candidates:
        if folder.exists():
            slides = sorted(folder.glob("*.svg"))
            if slides:
                return slides
    return []


def slide_names(project_path: Path | None) -> list[str]:
    return [path.name for path in slide_paths(project_path)]


def find_slide(project_path: Path, name: str) -> Path | None:
    for path in slide_paths(project_path):
        if path.name == name:
            return path
    return None


def render_svg_html(path: Path | None) -> str:
    if not path or not path.exists():
        return "<div class='empty'>No slide available yet.</div>"
    content = path.read_text(encoding="utf-8", errors="replace")
    return (
        "<div style='width:100%;height:720px;overflow:auto;background:#f6f7f9;"
        "border:1px solid #d9dde5;border-radius:8px;padding:12px'>"
        f"{content}"
        "</div>"
    )


def save_svg_source(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
