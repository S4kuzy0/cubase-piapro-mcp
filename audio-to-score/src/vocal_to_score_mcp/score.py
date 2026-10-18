"""Keep acoustic alignment, score timing, and singer phonemes distinct."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import mido
import textgrid

from .storage import AudioError, write_json

SILENCES = {"", "SP", "AP", "EP", "GS", "sil", "pau", "br"}


def alignment(path: Path, duration: float):
    grid = textgrid.TextGrid.fromFile(str(path))
    tiers = {tier.name: tier for tier in grid}
    if not {"texts", "words", "phones"}.issubset(tiers):
        raise AudioError("ALIGNMENT_LAYOUT", "TIFA did not produce texts, words and phones tiers")
    result = {}
    for name in ("texts", "words", "phones"):
        result[name] = []
        previous = 0.0
        for interval in tiers[name]:
            start, end = float(interval.minTime), float(interval.maxTime)
            if not all(math.isfinite(v) for v in (start, end)) or start < previous - 0.002 or end <= start or end > duration + 0.05:
                raise AudioError("INVALID_ALIGNMENT", "Invalid or overlapping TIFA intervals")
            previous = end
            result[name].append({"start": start, "end": end, "text": interval.mark})
    if not any(word["text"] not in SILENCES for word in result["words"]):
        raise AudioError("EMPTY_ALIGNMENT", "No aligned syllables")
    return result


def game_dataset(aligned: dict, duration: float, output: Path, language: str | None = None):
    """Each syllable boundary conditions GAME, with explicit intervening silence."""
    words = []
    cursor = 0.0
    for word in aligned["words"]:
        if word["start"] > cursor + 0.001:
            words.append({"start": cursor, "end": word["start"], "text": "SP", "lyric": ""})
        if word["end"] <= cursor:
            continue
        copy = dict(word)
        copy["start"] = max(cursor, word["start"])
        semantic = [item for item in aligned["texts"] if item["start"] < copy["end"] and item["end"] > copy["start"] and item["text"] not in SILENCES]
        copy["lyric"] = copy["text"]
        copy["lyric_is_reading"] = True
        if len(semantic) == 1 and word["text"] not in SILENCES:
            text = semantic[0]
            members = [item for item in aligned["words"] if item["text"] not in SILENCES and
                       item["start"] < text["end"] and item["end"] > text["start"]]
            copy["text_label"] = text["text"]
            if len(members) == 1:
                copy["lyric"] = text["text"]
                copy["lyric_is_reading"] = False
            elif language in ("zh", "yue") and len(members) == len(text["text"]) and all("\u3400" <= c <= "\u9fff" for c in text["text"]):
                copy["lyric"] = text["text"][members.index(word)]
                copy["lyric_is_reading"] = False
        words.append(copy)
        cursor = copy["end"]
    if cursor < duration - 0.001:
        words.append({"start": cursor, "end": duration, "text": "SP", "lyric": ""})
    # GAME consumes word timing, not lexical phonemes. A single voiced marker
    # per syllable expresses precisely that conditioning; it is never exported
    # as a Vocaloid phoneme or substituted for TIFA's retained phone tier.
    with output.open("x", newline="", encoding="utf8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["name", "ph_seq", "ph_dur", "ph_num"])
        writer.writeheader()
        writer.writerow({"name": "source", "ph_seq": " ".join("SP" if w["text"] in SILENCES else "v" for w in words),
                         "ph_dur": " ".join(f"{w['end'] - w['start']:.6f}" for w in words),
                         "ph_num": " ".join("1" for _ in words)})
    return words


def parse_game_aligned(path: Path, words: list[dict]):
    with path.open(encoding="utf8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise AudioError("GAME_LAYOUT", "Expected one GAME output record")
    row = rows[0]
    pitches = row["note_seq"].split()
    durations = [float(v) for v in row["note_dur"].split()]
    slurs = [int(v) for v in row["note_slur"].split()]
    if not len(pitches) == len(durations) == len(slurs):
        raise AudioError("GAME_LAYOUT", "Mismatched GAME output lengths")
    import re
    def midi_pitch(value):
        match = re.fullmatch(r"([A-G])(#|b)?(-?\d+)([+-]\d+)?", value)
        if not match:
            raise AudioError("GAME_PITCH", f"Unsupported note notation {value}")
        letter, accidental, octave, cents = match.groups()
        return (int(octave) + 1) * 12 + {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter] + {None: 0, "#": 1, "b": -1}[accidental] + int(cents or 0) / 100
    notes, position, word_index = [], 0.0, 0
    for pitch, length, slur in zip(pitches, durations, slurs):
        if not math.isfinite(length) or length <= 0:
            raise AudioError("GAME_DURATION", "Invalid note duration")
        while word_index < len(words) - 1 and position >= words[word_index]["end"] - 0.003:
            word_index += 1
        word = words[word_index]
        if position + length > word["end"] + 0.015:
            raise AudioError("WORD_NOTE_MISMATCH", "GAME note escapes its conditioned syllable")
        if pitch != "rest":
            if word["text"] in SILENCES:
                raise AudioError("SILENCE_NOTE", "GAME produced a voiced note in a labelled silence")
            notes.append({"start": position, "end": position + length, "pitch": midi_pitch(pitch),
                          "lyric": "-" if slur else word["lyric"], "syllable": word["text"],
                          "word_index": word_index, "continuation": bool(slur),
                          "lyric_is_reading": word.get("lyric_is_reading", False),
                          "text_label": word.get("text_label", word["lyric"])})
        position += length
    return validate_notes(notes)


def parse_game_raw(path: Path):
    with path.open(encoding="utf8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return validate_notes([{"start": float(row["onset"]), "end": float(row["offset"]),
                            "pitch": float(row["pitch"]), "lyric": None} for row in rows])


def validate_notes(notes):
    if not notes:
        raise AudioError("EMPTY_SCORE", "GAME produced no voiced notes")
    previous = 0.0
    for note in notes:
        start, end, pitch = note["start"], note["end"], note["pitch"]
        if not all(math.isfinite(v) for v in (start, end, pitch)) or start < previous - 0.002 or end <= start or not 0 <= pitch <= 127:
            raise AudioError("INVALID_NOTES", "Invalid, overlapping, or out-of-range notes")
        previous = end
    return notes


def export_midi(notes: list[dict], target: Path):
    file = mido.MidiFile(ticks_per_beat=480, charset="utf8")
    track = mido.MidiTrack()
    file.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    previous = 0
    for note in notes:
        onset = max(previous, round(note["start"] * 960))
        end = max(onset + 1, round(note["end"] * 960))
        delta = onset - previous
        if note.get("lyric") is not None:
            track.append(mido.MetaMessage("lyrics", text=note["lyric"], time=delta))
            delta = 0
        track.append(mido.Message("note_on", note=round(note["pitch"]), velocity=80, time=delta))
        track.append(mido.Message("note_off", note=round(note["pitch"]), time=end-onset))
        previous = end
    with target.open("xb") as stream:
        file.save(file=stream)
    # Reparse the serialized result, including its Unicode lyrics.
    recovered = mido.MidiFile(target, charset="utf8")
    if sum(message.type == "note_on" and message.velocity > 0 for message in recovered) != len(notes):
        raise AudioError("MIDI_READBACK", "Written MIDI note count mismatch")
