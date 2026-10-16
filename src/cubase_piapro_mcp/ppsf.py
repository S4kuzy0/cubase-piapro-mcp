"""Edit native legacy PPSF containers while retaining opaque chunks."""

from __future__ import annotations

from ._vendor.libresvip_legacy import model
from .errors import IntegrationError
from .vsqx import validate_change


def load(data: bytes):
    if data[:4] != b"PPSF":
        raise IntegrationError(
            "UNSUPPORTED_PPSF",
            "This adapter supports legacy binary PPSF, not NT ZIP projects.",
        )
    project = model.PpsfLegacyProject.parse(data)
    chunks = {chunk.magic: chunk for chunk in project.chunks}
    if "Events" not in chunks or "Tracks" not in chunks or "Clips" not in chunks:
        raise IntegrationError(
            "INCOMPLETE_PPSF", "The parser could not read the required chunks."
        )
    return project, chunks


def note_entries(project, chunks):
    events = chunks["Events"].data
    clips = {clip.index: clip for clip in chunks["Clips"].data.vocaloid3_note_clips}
    tracks = chunks["Tracks"].data.vocaloid3_event_tracks.data
    rows = []
    for ti, track in enumerate(tracks):
        for pi, clip_index in enumerate(track.data.clip_indices):
            if clip_index not in clips:
                continue
            clip = clips[clip_index].data.noteclip
            for event_index in clip.iclip.event_indices:
                event = events[event_index]
                if event.magic != "Vocaloid3NoteEvent":
                    continue
                note = event.data
                rows.append(
                    (
                        {
                            "id": f"e{event_index}",
                            "track_index": ti,
                            "part_index": pi,
                            "part_name": clip.iclip.name,
                            "part_start_tick": clip.offset,
                            "absolute_tick": clip.offset + note.tick,
                            "start_tick": note.tick,
                            "duration_tick": note.duration,
                            "note_num": note.note,
                            "velocity": note.velocity,
                            "lyric": note.lyric,
                            "phoneme": note.phoneme,
                        },
                        event,
                    )
                )
    return rows


def inspect(data: bytes, include_notes=False):
    project, chunks = load(data)
    rebuilt = model.PpsfLegacyProject.build(project)
    rows = [row for row, _ in note_entries(project, chunks)]
    result = {
        "format": "ppsf-legacy",
        "version": project.version_string,
        "track_count": len(chunks["Tracks"].data.vocaloid3_event_tracks.data),
        "note_count": len(rows),
        "binary_roundtrip_identical": rebuilt == data,
        "native_editable": rebuilt == data,
        "warnings": [
            "Native edits retain parsed opaque chunks. Reload in Piapro to update its live state."
        ],
    }
    if rebuilt != data:
        result["warnings"].append(
            "Binary round-trip differs: native writes are disabled for this file."
        )
    if include_notes:
        result["notes"] = rows
    return result


def editor_note_map(chunks):
    """Map redundant editor timing only when its ordering and values agree."""
    if "EditorDatas" not in chunks:
        raise IntegrationError(
            "PPSF_TIMING_MAP_UNSUPPORTED",
            "No editor timing data. Use VSQX for timing edits.",
        )
    tracks = chunks["Tracks"].data.vocaloid3_event_tracks.data
    editors = [
        t for t in chunks["EditorDatas"].data.track_datas if t.data.track_type == 3
    ]
    clips = {c.index: c for c in chunks["Clips"].data.vocaloid3_note_clips}
    result = {}
    if len(tracks) != len(editors):
        raise IntegrationError(
            "PPSF_TIMING_MAP_UNSUPPORTED",
            "Editor tracks do not match. Use VSQX for timing edits.",
        )
    for track, editor in zip(tracks, editors):
        if len(track.data.clip_indices) != len(editor.data.clip_datas):
            raise IntegrationError(
                "PPSF_TIMING_MAP_UNSUPPORTED",
                "Editor clips do not match. Use VSQX for timing edits.",
            )
        for ci, ec in zip(track.data.clip_indices, editor.data.clip_datas):
            indices = clips[ci].data.noteclip.iclip.event_indices
            entries = ec.data.element_array
            if len(indices) != len(entries):
                raise IntegrationError(
                    "PPSF_TIMING_MAP_UNSUPPORTED",
                    "Editor notes do not match. Use VSQX for timing edits.",
                )
            clip_end = max(
                (
                    chunks["Events"].data[i].data.tick
                    + chunks["Events"].data[i].data.duration
                    for i in indices
                    if chunks["Events"].data[i].magic == "Vocaloid3NoteEvent"
                ),
                default=0,
            )
            for i, entry in zip(indices, entries):
                event = chunks["Events"].data[i]
                if event.magic != "Vocaloid3NoteEvent":
                    continue
                if (
                    entry.magic != "EditorNoteData"
                    or i in result
                    or entry.data.pos_tick != event.data.tick
                ):
                    raise IntegrationError(
                        "PPSF_TIMING_MAP_UNSUPPORTED",
                        "Redundant note timing is ambiguous. Use VSQX for timing edits.",
                    )
                result[i] = (entry.data, clip_end)
    return result


def edit(data: bytes, changes: list[dict]) -> bytes:
    project, chunks = load(data)
    if model.PpsfLegacyProject.build(project) != data:
        raise IntegrationError(
            "PPSF_ROUNDTRIP_MISMATCH",
            "Native write requires an exact unmodified byte round-trip. Use VSQX instead.",
        )
    lookup = {row["id"]: event for row, event in note_entries(project, chunks)}
    events_model = model.ppsf_prefixed_array(model.PpsfEvent)
    original_events_length = len(
        events_model.build(chunks["Events"].data, version=project.version)
    )
    timing_changed = any("start_tick" in c or "duration_tick" in c for c in changes)
    editor_notes = editor_note_map(chunks) if timing_changed else {}
    fields = {
        "start_tick": "tick",
        "duration_tick": "duration",
        "note_num": "note",
        "velocity": "velocity",
        "lyric": "lyric",
        "phoneme": "phoneme",
    }
    seen = set()
    for change in changes:
        validate_change(change)
        key = change["id"]
        if key in seen or key not in lookup:
            raise IntegrationError(
                "INVALID_NOTE_ID", f"Missing or repeated native event id: {key}"
            )
        seen.add(key)
        event = lookup[key]
        old_payload_length = len(
            model.PpsfVocaloid3NoteEvent.build(event.data, version=project.version)
        )
        if "start_tick" in change or "duration_tick" in change:
            metadata, clip_end = editor_notes[int(key[1:])]
            if (metadata.dur_tick, metadata.dur_tick_copy) != (
                event.data.duration,
                event.data.duration,
            ):
                raise IntegrationError(
                    "PPSF_TIMING_MAP_UNSUPPORTED",
                    "This note has conflicting stored durations. Use VSQX for its timing edit.",
                )
            tick = change.get("start_tick", event.data.tick)
            duration = change.get("duration_tick", event.data.duration)
            if tick + duration > clip_end:
                raise IntegrationError(
                    "PPSF_TIMING_EXTENT",
                    "Native timing edits stay within the original note extent of this part. Use VSQX to extend it.",
                )
            metadata.pos_tick = tick
            metadata.dur_tick = metadata.dur_tick_copy = duration
        for field, value in change.items():
            if field != "id":
                event.data[fields[field]] = value
        event.size += (
            len(model.PpsfVocaloid3NoteEvent.build(event.data, version=project.version))
            - old_payload_length
        )
        if event.size > 65535:
            raise IntegrationError(
                "NOTE_TOO_LARGE", "PPSF note payload exceeds the binary size field."
            )
    # Preserve the native trailing bytes inside the sized Events chunk. The
    # parser ignores its zero terminator; Piapro itself requires it on import.
    chunks["Events"].size += (
        len(events_model.build(chunks["Events"].data, version=project.version))
        - original_events_length
    )
    result = model.PpsfLegacyProject.build(project)
    reread, reread_chunks = load(result)
    if model.PpsfLegacyProject.build(reread) != result:
        raise IntegrationError(
            "PPSF_WRITE_INVALID", "The edited file did not round-trip exactly."
        )
    # Unrelated chunks remain unchanged, including all opaque plugin data.
    for chunk in project.chunks:
        if chunk.magic != "Events" and not (
            timing_changed and chunk.magic == "EditorDatas"
        ):
            old = model.PpsfChunk.build(chunk, version=project.version)
            new = model.PpsfChunk.build(
                reread_chunks[chunk.magic], version=reread.version
            )
            if old != new:
                raise IntegrationError(
                    "PPSF_UNRELATED_CHANGE", f"Unexpected change to {chunk.magic}."
                )
    return result
