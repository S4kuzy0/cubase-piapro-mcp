"""Standard MIDI file edits with matched note-off and preserved metadata."""

from __future__ import annotations

import io
from collections import defaultdict, deque
import mido

from .errors import IntegrationError
from .vsqx import validate_change


def load(data):
    file = mido.MidiFile(file=io.BytesIO(data))
    if file.type == 2:
        raise IntegrationError(
            "UNSUPPORTED_MIDI", "Asynchronous type-2 MIDI is not supported."
        )
    events = []
    for track in file.tracks:
        tick = 0
        absolute = []
        for index, message in enumerate(track):
            tick += message.time
            absolute.append([tick, index, message])
        events.append(absolute)
    return file, events


def note_entries(file, tracks):
    rows = []
    for ti, events in enumerate(tracks):
        active = defaultdict(deque)
        for tick, index, message in events:
            if message.type == "note_on" and message.velocity:
                active[(message.channel, message.note)].append((tick, index, message))
            elif message.type == "note_off" or (
                message.type == "note_on" and not message.velocity
            ):
                queue = active[(message.channel, message.note)]
                if queue:
                    start, on_index, on = queue.popleft()
                    rows.append(
                        (
                            {
                                "id": f"t{ti}:e{on_index}",
                                "track_index": ti,
                                "part_index": 0,
                                "start_tick": start,
                                "duration_tick": tick - start,
                                "note_num": on.note,
                                "velocity": on.velocity,
                                "channel": on.channel,
                            },
                            ti,
                            on_index,
                            index,
                        )
                    )
        if any(active.values()):
            raise IntegrationError(
                "UNMATCHED_MIDI_NOTES",
                "A note has no matching note-off; editing would be ambiguous.",
            )
    return rows


def inspect(data: bytes, include_notes=False):
    file, events = load(data)
    rows = [row for row, *_ in note_entries(file, events)]
    result = {
        "format": "midi",
        "type": file.type,
        "ticks_per_beat": file.ticks_per_beat,
        "track_count": len(file.tracks),
        "note_count": len(rows),
    }
    if include_notes:
        result["notes"] = rows
    return result


def edit(data: bytes, changes: list[dict]) -> bytes:
    file, tracks = load(data)
    lookup = {
        row["id"]: (row, ti, on, off) for row, ti, on, off in note_entries(file, tracks)
    }
    seen = set()
    for change in changes:
        validate_change(change)
        key = change["id"]
        if key in seen or key not in lookup:
            raise IntegrationError("INVALID_NOTE_ID", key)
        seen.add(key)
        if {"lyric", "phoneme"} & change.keys():
            raise IntegrationError(
                "UNSUPPORTED_MIDI_FIELD",
                "Use VSQX for phonemes or note-attached lyrics.",
            )
        row, ti, on, off = lookup[key]
        updated = row | change
        tracks[ti][on][0] = updated["start_tick"]
        tracks[ti][off][0] = updated["start_tick"] + updated["duration_tick"]
        tracks[ti][on][2].note = updated["note_num"]
        tracks[ti][off][2].note = updated["note_num"]
        tracks[ti][on][2].velocity = updated["velocity"]
    for track, events in zip(file.tracks, tracks):
        events.sort(key=lambda entry: (entry[0], entry[1]))
        track.clear()
        previous = 0
        end = []
        for tick, _, message in events:
            if message.type == "end_of_track":
                end.append(tick)
                continue
            track.append(message.copy(time=tick - previous))
            previous = tick
        track.append(
            mido.MetaMessage(
                "end_of_track", time=max(0, max(end, default=previous) - previous)
            )
        )
    output = io.BytesIO()
    file.save(file=output)
    inspect(output.getvalue())
    return output.getvalue()
