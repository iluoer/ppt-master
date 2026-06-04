"""Optional Supabase project persistence for hosted deployments."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import quote
import zipfile

import requests


def _zip_project(project_path: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="ppt-master-storage-"))
    archive = temp_dir / f"{project_path.name}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in project_path.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(project_path))
    return archive


def _supabase_enabled() -> bool:
    selected = os.environ.get("PERSISTENCE_BACKEND", "supabase").strip().lower()
    if selected in {"none", "off", "disabled"}:
        return False
    return bool(os.environ.get("SUPABASE_URL", "").strip() and os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip())


def _supabase_config() -> tuple[str, str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.environ.get("SUPABASE_BUCKET", "ppt-master").strip() or "ppt-master"
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
    return url, key, bucket


def _supabase_headers(content_type: str | None = None) -> dict[str, str]:
    _, key, _ = _supabase_config()
    headers = {
        "Authorization": f"Bearer {key}",
        "apikey": key,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _supabase_storage_url(path: str) -> str:
    url, _, _ = _supabase_config()
    return f"{url}/storage/v1/{path.lstrip('/')}"


def _supabase_object_path(path: str) -> str:
    _, _, bucket = _supabase_config()
    return f"object/{quote(bucket, safe='')}/{quote(path, safe='/')}"


def _ensure_supabase_bucket() -> None:
    _, _, bucket = _supabase_config()
    response = requests.post(
        _supabase_storage_url("bucket"),
        headers=_supabase_headers("application/json"),
        json={"id": bucket, "name": bucket, "public": False},
        timeout=30,
    )
    if response.status_code in {200, 201, 409}:
        return
    if response.status_code == 400 and "already" in response.text.lower():
        return
    response.raise_for_status()


def _upload_supabase_file(local_path: Path, object_path: str, content_type: str) -> None:
    _ensure_supabase_bucket()
    with local_path.open("rb") as fh:
        response = requests.post(
            _supabase_storage_url(_supabase_object_path(object_path)),
            headers={
                **_supabase_headers(content_type),
                "x-upsert": "true",
            },
            data=fh,
            timeout=180,
        )
    response.raise_for_status()


def _upload_supabase_json(payload: dict[str, Any], object_path: str) -> None:
    _ensure_supabase_bucket()
    response = requests.post(
        _supabase_storage_url(_supabase_object_path(object_path)),
        headers={
            **_supabase_headers("application/json"),
            "x-upsert": "true",
        },
        data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        timeout=60,
    )
    response.raise_for_status()


def _download_supabase_object(object_path: str) -> bytes | None:
    response = requests.get(
        _supabase_storage_url(_supabase_object_path(object_path)),
        headers=_supabase_headers(),
        timeout=180,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def _read_supabase_index() -> dict[str, Any]:
    raw = _download_supabase_object("projects/index.json")
    if raw is None:
        return {"projects": []}
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"projects": []}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return {"projects": []}
    return data


def _write_supabase_index(project_record: dict[str, Any]) -> None:
    index = _read_supabase_index()
    projects = [item for item in index.get("projects", []) if item.get("project_name") != project_record["project_name"]]
    projects.insert(0, project_record)
    projects.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    index["projects"] = projects[:100]
    _upload_supabase_json(index, "projects/index.json")


def persist_project(project_path: Path, latest_pptx: str | None) -> str:
    if not _supabase_enabled():
        return "Supabase storage not configured; skipped persistent upload."
    try:
        archive = _zip_project(project_path)
        prefix = f"projects/{project_path.name}"
        _upload_supabase_file(archive, f"{prefix}/project.zip", "application/zip")
        if latest_pptx:
            _upload_supabase_file(
                Path(latest_pptx),
                f"{prefix}/latest.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )

        manifest = {
            "provider": "supabase",
            "project_name": project_path.name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "latest_pptx": Path(latest_pptx).name if latest_pptx else None,
        }
        _upload_supabase_json(manifest, f"{prefix}/manifest.json")
        _write_supabase_index(manifest)
        _, _, bucket = _supabase_config()
        return f"Uploaded project snapshot to Supabase bucket: {bucket}/{prefix}"
    except Exception as exc:  # noqa: BLE001 - persistence must not break the generation result.
        return f"Supabase upload failed: {type(exc).__name__}: {exc}"


def list_remote_projects() -> list[str]:
    if not _supabase_enabled():
        return []
    try:
        index = _read_supabase_index()
    except Exception:
        return []
    names = [item.get("project_name") for item in index.get("projects", []) if item.get("project_name")]
    return list(dict.fromkeys(names))


def restore_project(project_name: str) -> Path:
    if not _supabase_enabled():
        raise RuntimeError("Supabase storage is not configured.")
    if "/" in project_name or "\\" in project_name or ".." in project_name:
        raise RuntimeError(f"Invalid project name: {project_name}")

    from .projects import project_parent

    raw = _download_supabase_object(f"projects/{project_name}/project.zip")
    if raw is None:
        raise RuntimeError(f"Supabase project snapshot not found: {project_name}")

    temp_dir = Path(tempfile.mkdtemp(prefix="ppt-master-supabase-"))
    archive = temp_dir / f"{project_name}.zip"
    archive.write_bytes(raw)
    target = project_parent() / project_name
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.infolist():
            out = (target / member.filename).resolve()
            try:
                out.relative_to(target.resolve())
            except ValueError:
                raise RuntimeError(f"Unsafe archive member: {member.filename}")
            zf.extract(member, target)
    return target
