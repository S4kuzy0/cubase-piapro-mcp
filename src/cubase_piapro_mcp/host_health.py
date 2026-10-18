"""Refuse live control of a Cubase process that has produced a crash dump."""

from __future__ import annotations

import os
import struct
from pathlib import Path

from .errors import IntegrationError


def _hosts():
    if os.name != "nt":
        return []
    from . import windows
    return [{"pid": pid} for pid in sorted({w["pid"] for w in windows.windows() if "pid" in w})]


def _process_started(pid):
    from . import windows
    api, _, process = windows.api()
    handle = api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    try:
        return process.GetProcessTimes(handle)["CreationTime"].timestamp()
    finally:
        handle.Close()


def _dump_directory():
    from win32com.shell import shell, shellcon
    documents = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PERSONAL, None, 0))
    return documents / "Steinberg" / "CrashDumps"


def _dump_owner(path):
    """Read only a bounded header/directory and MiscInfo; partial reports stay unsafe."""
    try:
        with path.open("rb") as source:
            header = source.read(32)
            if len(header) != 32 or header[:4] != b"MDMP":
                return None
            count, directory = struct.unpack_from("<II", header, 8)
            size = os.fstat(source.fileno()).st_size
            if not 0 < count <= 256 or not 32 <= directory <= min(size - 12 * count, 8 * 1024 * 1024):
                return None
            source.seek(directory)
            entries = source.read(count * 12)
            for offset in range(0, len(entries), 12):
                kind, length, position = struct.unpack_from("<III", entries, offset)
                if kind != 15:
                    continue
                if length < 24 or not 32 <= position <= size - 24:
                    return None
                source.seek(position)
                record_size, flags, pid, created = struct.unpack("<IIII", source.read(16))
                if record_size < 24 or record_size > length or not flags & 1 or not pid:
                    return None
                return pid, created if flags & 2 and created else None
    except (OSError, ValueError, struct.error):
        return None
    return None


def require_healthy():
    """File timestamps and OS process times only; never query MIDI/native host objects.

    An old report does not block a restarted process. Owner metadata excludes
    other processes and reused PIDs. A new unparseable report fails closed.
    This check cannot detect a fault before Cubase has written its report.
    """
    try:
        hosts = _hosts()
        if not hosts:
            return
        processes = {host["pid"]: _process_started(host["pid"]) for host in hosts}
        started = min(processes.values())
        directory = _dump_directory()
        if not directory.exists():
            return
        for report in directory.iterdir():
            if not report.name.lower().startswith("cubase ") or report.suffix.lower() != ".dmp":
                continue
            if report.stat().st_mtime >= started:
                owner = _dump_owner(report)
                if owner is not None:
                    pid, created = owner
                    if pid not in processes or (created is not None and abs(processes[pid] - created) >= 1):
                        continue
                raise IntegrationError(
                    "HOST_CRASH_DETECTED",
                    "A new Cubase crash report marks this host unsafe: " + report.name
                    + ". No further MIDI or live-editor operation is allowed. Preserve unsaved work, "
                    "inspect the report and restart Cubase with the stable driver before retrying.",
                )
    except IntegrationError:
        raise
    except Exception as exc:
        raise IntegrationError(
            "HOST_HEALTH_UNVERIFIED",
            "Could not verify Cubase process/crash-report times; no live command was authorized. "
            + str(exc),
        ) from exc
