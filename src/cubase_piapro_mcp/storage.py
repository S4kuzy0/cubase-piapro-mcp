"""Atomic writes, optimistic concurrency and durable recovery copies."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .errors import IntegrationError


def state_dir() -> Path:
    base = os.environ.get("CUBASE_PIAPRO_STATE_DIR")
    if base:
        root = Path(base).expanduser().resolve()
    else:
        root = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CubasePiaproMCP"
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: str | Path) -> tuple[Path, bytes, str]:
    file = Path(path).expanduser().resolve(strict=True)
    if not file.is_file():
        raise IntegrationError("NOT_A_FILE", str(file))
    if file.stat().st_size > 256 * 1024 * 1024:
        raise IntegrationError("FILE_TOO_LARGE", "The supported file limit is 256 MiB.")
    data = file.read_bytes()
    return file, data, digest(data)


def verify_hash(path: Path, expected: str) -> None:
    if not expected or digest(path.read_bytes()) != expected.lower():
        raise IntegrationError(
            "FILE_CHANGED",
            "Inspect the latest file and use its SHA-256 before editing.",
        )


@contextmanager
def file_lock(path: Path):
    key = digest(str(path).casefold().encode())
    lock = state_dir() / "locks" / f"{key}.lock"
    lock.parent.mkdir(exist_ok=True)
    with lock.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise IntegrationError(
                    "BUSY", "Another MCP process is writing this target."
                ) from exc
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def backup_file(path: Path) -> dict:
    original = path.read_bytes()
    sha = digest(original)
    folder = state_dir() / "backups" / sha[:16]
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / path.name
    if not copy.exists():
        with copy.open("xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    if digest(copy.read_bytes()) != sha:
        raise IntegrationError(
            "BACKUP_INVALID", "The recovery copy did not match its source."
        )
    record = {
        "original_path": str(path),
        "backup_path": str(copy),
        "sha256": sha,
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    with (state_dir() / "backups.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.flush()
        os.fsync(log.fileno())
    return record


def write_result(
    source: Path | None, output: str, data: bytes, expected_sha256: str | None = None
) -> dict:
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    same = source is not None and target == source
    with file_lock(target):
        if source is not None:
            verify_hash(source, expected_sha256 or "")
        if target.exists() and not same:
            raise IntegrationError(
                "TARGET_EXISTS",
                "Choose a new output path; existing unrelated files are not replaced.",
            )
        backup = backup_file(target) if target.exists() else None
        fd, temporary = tempfile.mkstemp(
            prefix=".mcp-", suffix=target.suffix, dir=target.parent
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if source is not None:
                verify_hash(source, expected_sha256 or "")
            if same:
                os.replace(temporary, target)
            else:
                # Windows rename refuses an existing destination, including a concurrent create.
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                    os.unlink(temporary)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {
            "path": str(target),
            "sha256": digest(data),
            "bytes": len(data),
            "backup": backup,
        }


def list_backups(original_path: str | None = None) -> list[dict]:
    index = state_dir() / "backups.jsonl"
    records = (
        [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
        if index.exists()
        else []
    )
    if original_path:
        wanted = str(Path(original_path).resolve()).casefold()
        records = [
            item for item in records if item["original_path"].casefold() == wanted
        ]
    return records[-100:]


def restore_backup(
    backup_path: str, target_path: str, expected_current_sha256: str
) -> dict:
    copy = Path(backup_path).resolve(strict=True)
    records = [
        item for item in list_backups(target_path) if Path(item["backup_path"]) == copy
    ]
    if not records or digest(copy.read_bytes()) != records[-1]["sha256"]:
        raise IntegrationError(
            "BACKUP_INVALID", "Use a recorded, verified backup for this exact target."
        )
    target = Path(target_path).resolve(strict=True)
    return write_result(target, str(target), copy.read_bytes(), expected_current_sha256)
