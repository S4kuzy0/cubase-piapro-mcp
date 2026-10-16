"""Common file tool operations and source-hash contracts."""

from __future__ import annotations

from pathlib import Path

from . import cpr, midi, ppsf, vsqx
from .errors import IntegrationError
from .storage import read_file, write_result


def adapter(path):
    adapters = {".vsqx": vsqx, ".ppsf": ppsf, ".mid": midi, ".midi": midi}
    if path.suffix.lower() not in adapters:
        raise IntegrationError(
            "UNSUPPORTED_FORMAT",
            "Supported editing formats: VSQX, legacy PPSF and standard MIDI.",
        )
    return adapters[path.suffix.lower()]


def read_expected(path, expected_sha256):
    source, data, sha = read_file(path)
    if sha != expected_sha256.lower():
        raise IntegrationError(
            "FILE_CHANGED", "The input differs from the inspected source hash."
        )
    return source, data, sha


def inspect_project(
    path: str, include_notes: bool = False, offset: int = 0, limit: int = 200
):
    source, data, sha = read_file(path)
    result = (
        cpr.inspect(source)
        if source.suffix.lower() == ".cpr"
        else adapter(source).inspect(data, include_notes)
    )
    if "notes" in result:
        if offset < 0 or not 1 <= limit <= 2000:
            raise IntegrationError(
                "INVALID_PAGE", "Use nonnegative offset and a limit from 1 to 2000."
            )
        result["notes"] = result["notes"][offset : offset + limit]
        result["next_offset"] = (
            offset + limit if offset + limit < result["note_count"] else None
        )
    return {"path": str(source), "sha256": sha, "bytes": len(data)} | result


def edit_notes(path: str, output_path: str, changes: list[dict], expected_sha256: str):
    source, data, sha = read_expected(path, expected_sha256)
    if not changes or len(changes) > 10000:
        raise IntegrationError(
            "INVALID_CHANGESET", "Supply between 1 and 10000 note changes."
        )
    if source.suffix.lower() != Path(output_path).suffix.lower():
        raise IntegrationError(
            "FORMAT_MISMATCH",
            "Editing preserves the input format; use the same file extension.",
        )
    result = adapter(source).edit(data, changes)
    written = write_result(source, output_path, result, expected_sha256)
    return written | {
        "changed_notes": len(changes),
        "source_sha256": sha,
        "live_state_updated": False,
    }


def transpose(
    path: str,
    output_path: str,
    semitones: int,
    expected_sha256: str,
    track_index: int | None = None,
):
    if not -127 <= semitones <= 127:
        raise IntegrationError(
            "INVALID_TRANSPOSE", "Semitone offset must be between -127 and 127."
        )
    source, data, _ = read_expected(path, expected_sha256)
    rows = adapter(source).inspect(data, True)["notes"]
    changes = [
        {"id": row["id"], "note_num": row["note_num"] + semitones}
        for row in rows
        if track_index is None or row["track_index"] == track_index
    ]
    return edit_notes(path, output_path, changes, expected_sha256)


def set_curve(
    path: str,
    output_path: str,
    expected_sha256: str,
    track_index: int,
    part_index: int,
    controller: str,
    points: list[dict],
    mode: str = "replace",
):
    source, data, _ = read_expected(path, expected_sha256)
    if source.suffix.lower() != ".vsqx" or Path(output_path).suffix.lower() != ".vsqx":
        raise IntegrationError(
            "CURVE_FORMAT",
            "Curve insertion/replacement currently requires VSQX input and output.",
        )
    result = vsqx.curve(data, track_index, part_index, controller, points, mode)
    return write_result(source, output_path, result, expected_sha256) | {
        "live_state_updated": False
    }


def arrange_notes(
    path: str,
    output_path: str,
    expected_sha256: str,
    additions: list[dict] | None = None,
    deletions: list[str] | None = None,
    track_index: int = 0,
    part_index: int = 0,
):
    source, data, _ = read_expected(path, expected_sha256)
    if source.suffix.lower() != ".vsqx" or Path(output_path).suffix.lower() != ".vsqx":
        raise IntegrationError(
            "ARRANGE_FORMAT", "Note insertion/deletion currently requires VSQX."
        )
    result = vsqx.delete_notes(data, deletions) if deletions else data
    if additions:
        result = vsqx.add_notes(result, track_index, part_index, additions)
    if not additions and not deletions:
        raise IntegrationError("EMPTY_CHANGE", "Supply additions or deletions.")
    return write_result(source, output_path, result, expected_sha256) | {
        "live_state_updated": False
    }


def create_score(
    voice_source_path: str,
    output_path: str,
    entries: list[dict],
    voice_bank: int,
    voice_program: int,
    tempo_bpm: float,
    track_name: str,
):
    _, data, _ = read_file(voice_source_path)
    if Path(output_path).suffix.lower() != ".vsqx":
        raise IntegrationError("FORMAT_MISMATCH", "New scores are written as VSQX4.")
    result = vsqx.create(
        data, entries, voice_bank, voice_program, tempo_bpm, track_name
    )
    return write_result(None, output_path, result) | {
        "live_state_updated": False,
        "note_count": len(entries),
    }
