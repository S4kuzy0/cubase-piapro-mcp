"""Local standalone CPR templates. Native project bytes are copied, never patched."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .errors import IntegrationError
from .storage import digest, file_lock, read_file, state_dir, write_result


def _directory(template_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", template_id):
        raise IntegrationError("INVALID_TEMPLATE_ID", "Use a lowercase template identifier, without path separators.")
    return state_dir() / "project-templates" / template_id


def load(template_id: str) -> tuple[dict, Path, bytes]:
    directory = _directory(template_id)
    try:
        manifest = json.loads((directory / "template.json").read_text(encoding="utf-8"))
        project = directory / "project.cpr"
        data = project.read_bytes()
        if (
            manifest.get("schema") != 1
            or manifest.get("id") != template_id
            or manifest.get("standalone_no_external_media") is not True
            or not project.resolve().is_relative_to(directory.resolve())
            or digest(data) != manifest.get("sha256")
            or data[:4] not in (b"RIF2", b"RIFF")
        ):
            raise ValueError("Template manifest or bytes do not match")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise IntegrationError("TEMPLATE_UNAVAILABLE", f"Install or repair local template {template_id}: {exc}") from exc
    return manifest, project, data


def list_templates() -> dict:
    root = state_dir() / "project-templates"
    templates = []
    if root.exists():
        for directory in sorted(root.iterdir()):
            if directory.is_dir() and (directory / "template.json").exists():
                manifest, _, _ = load(directory.name)
                templates.append(manifest)
    return {"templates": templates, "scope": "local standalone CPR templates; commercial plugins and voicebanks are not bundled"}


def register(source_path: str, expected_sha256: str, template_id: str,
             description: str, *, standalone_no_external_media: bool,
             expected_tracks: list[dict] | None = None) -> dict:
    """Operator setup only: external media absence must be checked in Cubase."""
    source, data, sha = read_file(source_path)
    if source.suffix.lower() != ".cpr" or sha != expected_sha256.lower():
        raise IntegrationError("TEMPLATE_SOURCE_CHANGED", "Inspect the saved CPR and use its current SHA-256.")
    if not standalone_no_external_media:
        raise IntegrationError("TEMPLATE_MEDIA_UNSUPPORTED", "Only standalone projects without external audio/video are supported. CPR parsing cannot prove this automatically.")
    if data[:4] not in (b"RIF2", b"RIFF"):
        raise IntegrationError("TEMPLATE_NATIVE_FORMAT_INVALID", "Expected a native RIF2/RIFF CPR container. No binary conversion is attempted.")
    directory = _directory(template_id)
    with file_lock(directory):
        if directory.exists():
            raise IntegrationError("TEMPLATE_EXISTS", "Existing template versions are preserved. Choose a new identifier.")
        directory.mkdir(parents=True)
        copy = write_result(source, str(directory / "project.cpr"), data, sha)
        manifest = {
            "schema": 1, "id": template_id, "description": description,
            "sha256": copy["sha256"], "standalone_no_external_media": True,
            "media_check": "declared by the operator after inspecting the native project",
            "expected_tracks": expected_tracks or [],
            "created_beijing": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        }
        write_result(None, str(directory / "template.json"), json.dumps(
            manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return manifest


def project_name(value: str) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 80
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
        or value.endswith((".", " "))
        or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value.split(".")[0], re.I)
    ):
        raise IntegrationError("INVALID_PROJECT_NAME", "Use a valid Windows filename without an extension or path separators.")
    return value


def create_copy(template_id: str, project_directory: str, name: str | None = None) -> dict:
    manifest, source, data = load(template_id)
    directory = Path(project_directory).expanduser()
    if not directory.is_absolute():
        raise IntegrationError("ABSOLUTE_PATH_REQUIRED", "Specify the complete new project directory.")
    directory = directory.resolve()
    name = project_name(name if name is not None else directory.name)
    with file_lock(directory):
        if directory.exists():
            raise IntegrationError("PROJECT_DIRECTORY_EXISTS", "Use a new project directory; existing project content is never replaced.")
        directory.mkdir(parents=True)
        copied = write_result(source, str(directory / (name + ".cpr")), data, manifest["sha256"])
        record = {
            "template_id": template_id, "template_sha256": manifest["sha256"],
            "project_path": copied["path"], "project_created": True,
            "live_state_verified": False, "expected_tracks": manifest.get("expected_tracks", []),
        }
        write_result(None, str(directory / ".mcp-project.local.json"), json.dumps(
            record, ensure_ascii=False, indent=2).encode("utf-8"))
    return copied | record
