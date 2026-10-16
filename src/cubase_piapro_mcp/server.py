"""Local stdio MCP server. All logging stays off stdout."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from . import __version__, projects, storage, live_midi, windows

mcp = MCPServer(
    "cubase-piapro",
    instructions=(
        "Data-first Cubase/Piapro tools. Inspect files first and use their SHA-256 for writes. "
        "Prefer native PPSF/VSQX/MIDI file edits over UI operations. File edits do not update the live editor. "
        "CPR inspection is partial and read-only; its indexes are not live track IDs. "
        "Import makes a snapshot first, then requires export/readback verification. "
        "Live mixer controls apply only to the selected Cubase channel. "
        "Do not claim success when verified/connected is false or an error is returned."
    ),
)
READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


class NoteChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    start_tick: int | None = None
    duration_tick: int | None = None
    note_num: int | None = None
    velocity: int | None = None
    lyric: str | None = None
    phoneme: str | None = None


class CurvePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tick: int
    value: int


class NewNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_tick: int
    duration_tick: int
    note_num: int
    velocity: int = 64
    lyric: str = "あ"
    phoneme: str = "a"


@mcp.tool(annotations=READ)
def get_capabilities() -> dict:
    """List implemented operations and exact limitations without changing any app."""
    return {
        "version": __version__,
        "transport": "stdio",
        "file_editing": ["vsqx3", "vsqx4", "legacy-ppsf-notes", "standard-midi"],
        "curves": "VSQX PIT/PBS/DYN/BRE/BRI/CLE/GEN/OPE/POR",
        "cpr": "partial read-only",
        "native_ppsf_gate": "exact binary roundtrip; sizes preserve native tails; only events and matching editor timing copies may change",
        "live_control": "selected channel and transport over MIDI Remote, only if live feedback is available",
        "piapro_ui": "dynamic Win32 menus and standard dialogs; optional verified title-bar click for activation only",
        "focus_click_enabled": os.environ.get("CUBASE_PIAPRO_FOCUS_CLICK") == "1",
        "backups": True,
        "concurrency": "required source SHA-256 plus per-target cross-process locks",
        "limitations": [
            "No arbitrary native CPR writes",
            "No Piapro NT ZIP editing",
            "Note insertion/deletion requires VSQX",
            "No automatic claim of live state after a file edit",
        ],
        "state_dir": str(storage.state_dir()),
    }


@mcp.tool(annotations=READ)
def inspect_project(
    path: str, include_notes: bool = False, offset: int = 0, limit: int = 200
) -> dict:
    """Read VSQX, legacy PPSF, MIDI or partial CPR. Returns current SHA-256; note pages have stable IDs for this hash."""
    return projects.inspect_project(path, include_notes, offset, limit)


@mcp.tool(annotations=WRITE)
def edit_notes(
    path: str, output_path: str, changes: list[NoteChange], expected_sha256: str
) -> dict:
    """Edit specified existing notes directly. Same-path overwrite requires the inspected hash and creates a durable backup. Other outputs must be new."""
    return projects.edit_notes(
        path,
        output_path,
        [change.model_dump(exclude_none=True) for change in changes],
        expected_sha256,
    )


@mcp.tool(annotations=WRITE)
def transpose_notes(
    path: str,
    output_path: str,
    semitones: int,
    expected_sha256: str,
    track_index: int | None = None,
) -> dict:
    """Transpose existing notes in a file or one track, preserving all unrelated data; live editor is not changed."""
    return projects.transpose(
        path, output_path, semitones, expected_sha256, track_index
    )


@mcp.tool(annotations=WRITE)
def set_control_curve(
    path: str,
    output_path: str,
    expected_sha256: str,
    track_index: int,
    part_index: int,
    controller: str,
    points: list[CurvePoint],
    mode: str = "replace",
) -> dict:
    """Replace/merge one VSQX controller curve. PIT uses -8192..8191; PBS 0..24; other controllers 0..127. Ticks are part-relative."""
    return projects.set_curve(
        path,
        output_path,
        expected_sha256,
        track_index,
        part_index,
        controller,
        [point.model_dump() for point in points],
        mode,
    )


@mcp.tool(annotations=READ)
def read_control_curve(
    path: str,
    track_index: int,
    part_index: int,
    controller: str,
    offset: int = 0,
    limit: int = 500,
) -> dict:
    """Read one VSQX controller curve as part-relative tick/value points, with source SHA-256 and pagination."""
    from . import vsqx
    from .errors import IntegrationError

    source, data, sha = storage.read_file(path)
    if source.suffix.lower() != ".vsqx" or offset < 0 or not 1 <= limit <= 2000:
        raise IntegrationError(
            "INVALID_CURVE_REQUEST",
            "Use a VSQX file, nonnegative offset, and limit from 1 to 2000.",
        )
    points = vsqx.read_curve(data, track_index, part_index, controller)
    return {
        "sha256": sha,
        "controller": controller,
        "point_count": len(points),
        "points": points[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(points) else None,
    }


@mcp.tool(annotations=WRITE)
def set_vsqx_singer(
    path: str,
    output_path: str,
    expected_sha256: str,
    track_index: int,
    part_index: int,
    voice_bank: int,
    voice_program: int,
) -> dict:
    """Set all singer events in one VSQX part to an existing voice-table entry. Does not install voicebanks or change the live editor."""
    from . import vsqx
    from .errors import IntegrationError

    source, data, _ = projects.read_expected(path, expected_sha256)
    if source.suffix.lower() != ".vsqx" or Path(output_path).suffix.lower() != ".vsqx":
        raise IntegrationError(
            "FORMAT_MISMATCH", "Singer changes currently require VSQX."
        )
    result = vsqx.set_singer(data, track_index, part_index, voice_bank, voice_program)
    return storage.write_result(source, output_path, result, expected_sha256) | {
        "live_state_updated": False
    }


@mcp.tool(annotations=WRITE)
def arrange_vsqx_notes(
    path: str,
    output_path: str,
    expected_sha256: str,
    additions: list[NewNote] | None = None,
    deletions: list[str] | None = None,
    track_index: int = 0,
    part_index: int = 0,
) -> dict:
    """Insert/delete VSQX notes directly. Additions inherit the first remaining note's style. IDs of deletions refer to the inspected input; additions use part-relative ticks."""
    return projects.arrange_notes(
        path,
        output_path,
        expected_sha256,
        [note.model_dump() for note in additions] if additions else None,
        deletions,
        track_index,
        part_index,
    )


@mcp.tool(annotations=WRITE)
def create_vsqx(
    voice_source_path: str,
    output_path: str,
    notes: list[NewNote],
    voice_bank: int = 0,
    voice_program: int = 0,
    tempo_bpm: float = 120.0,
    track_name: str = "Vocal",
) -> dict:
    """Create a new VSQX4 score using only the voice table from a local VSQX4. No music is copied. Select an existing voice bank/program; ticks are relative to the new part."""
    return projects.create_score(
        voice_source_path,
        output_path,
        [note.model_dump() for note in notes],
        voice_bank,
        voice_program,
        tempo_bpm,
        track_name,
    )


@mcp.tool(annotations=READ)
def list_recovery_copies(original_path: str | None = None) -> dict:
    """List up to 100 recorded recovery copies, optionally for an exact original path."""
    return {"backups": storage.list_backups(original_path)}


@mcp.tool(annotations=WRITE)
def restore_recovery_copy(
    backup_path: str, target_path: str, expected_current_sha256: str
) -> dict:
    """Restore a recorded backup to its original target, after checking current hash and backing up the current version."""
    return storage.restore_backup(backup_path, target_path, expected_current_sha256)


@mcp.tool(annotations=READ)
def daw_get_status() -> dict:
    """Read Cubase/Piapro windows and MIDI feedback status; no focus, clicks or mutations."""
    return {
        "windows": windows.windows() if os.name == "nt" else [],
        "midi": live_midi.get_state(),
    }


@mcp.tool(annotations=READ)
def piapro_list_menus() -> dict:
    """Read the actual open Piapro editor's menu names and enabled states; no hard-coded handles."""
    return {"menus": windows.list_menus()}


@mcp.tool(annotations=WRITE)
def piapro_export(kind: str, output_path: str, timeout_seconds: int = 30) -> dict:
    """Export current Piapro state as vsqx/ppsf/wav through native menu/dialog commands. WAV in VSTi exports the selected Piapro track. Output must be new. Optional title-bar focus fallback only; file is validated before success."""
    return windows.export_file(kind, output_path, timeout_seconds)


@mcp.tool(annotations=WRITE)
def piapro_import(
    path: str, expected_sha256: str, initialize_empty: bool = False
) -> dict:
    """Import an immutable score copy after exporting a native recovery snapshot. Export/readback is required. VSQX/MIDI may append tracks. initialize_empty permits a first import only when empty-menu state and stopped MIDI host feedback agree."""
    return windows.import_file(path, expected_sha256, allow_empty=initialize_empty)


@mcp.tool(annotations=WRITE)
def cubase_control(action: str, value: float | None = None) -> dict:
    """Control play/stop/record or selected channel volume/pan/mute/solo via MIDI Remote. Success requires host feedback. Record starts recording."""
    return live_midi.control(action, value)


def main():
    logging.basicConfig(level=logging.WARNING)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
