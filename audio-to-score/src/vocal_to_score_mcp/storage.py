"""Small, local files are the boundary between the server and model processes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

from mcp.server.mcpserver.exceptions import ToolError


class AudioError(ToolError, ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def e_path(value: str, *, exists=True) -> Path:
    path = Path(value).resolve(strict=exists)
    if path.drive.lower() != "e:":
        raise AudioError("E_DRIVE_REQUIRED", "Runtime, models, caches and outputs must resolve to E:")
    return path


def load_config(path: Path):
    data = read_json(path)
    data["root"] = str(e_path(data["root"]))
    for name in ("ffmpeg", "ffprobe"):
        data[name] = str(e_path(data[name]))
    for name in ("separation", "game", "tifa"):
        entry = data[name]
        for field in ("python", "source", "model"):
            entry[field] = str(e_path(entry[field]))
        if len(entry.get("sha256", "")) != 64:
            raise AudioError("MODEL_IDENTITY_REQUIRED", f"Missing pinned {name} model SHA-256")
    if data.get("device", "cpu") not in ("cpu", "cuda"):
        raise AudioError("INVALID_DEVICE", "Select cpu or cuda in the local configuration")
    return data


def job_directory(config: dict, identifier: str) -> Path:
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise AudioError("INVALID_JOB_ID", "Use a job ID returned by start_vocal_job")
    root = e_path(config["root"]) / "jobs"
    target = (root / identifier).resolve()
    if not target.is_relative_to(root.resolve()):
        raise AudioError("INVALID_JOB_ID", "Job path escapes the job root")
    return target


def describe(path: Path):
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
