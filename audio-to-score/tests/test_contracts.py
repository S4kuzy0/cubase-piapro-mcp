import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import mido
import pytest
import textgrid

from vocal_to_score_mcp import jobs
from vocal_to_score_mcp.score import alignment, export_midi, game_dataset, parse_game_aligned, validate_notes
from vocal_to_score_mcp.storage import AudioError, job_directory, sha256
from vocal_to_score_mcp.worker import Worker


def test_beijing_timestamp_without_os_timezone_database():
    assert jobs.now().endswith("+08:00")


def test_changed_input_never_starts_a_model(tmp_path, monkeypatch):
    source = tmp_path / "input.wav"
    source.write_bytes(b"changed audio")
    monkeypatch.setattr(jobs, "load_config", lambda path: {"root": str(tmp_path)})
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(AudioError, match="SHA-256"):
        jobs.start(tmp_path / "config", operation="separate", audio_path=str(source), expected_sha256="0"*64)
    assert not (tmp_path / "jobs").exists()
    assert source.read_bytes() == b"changed audio"


@pytest.mark.parametrize("start,end", [(float("nan"), None), (0, float("inf")), (-1, 2), (2, 1), (0, 61)])
def test_invalid_crops_rejected_before_copy(tmp_path, monkeypatch, start, end):
    source = tmp_path / "a.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(jobs, "load_config", lambda path: {"root": str(tmp_path)})
    with pytest.raises(AudioError):
        jobs.start(tmp_path / "config", operation="separate", audio_path=str(source), expected_sha256=sha256(source),
                   crop_start=start, crop_end=end)
    assert not (tmp_path / "jobs").exists()


@pytest.mark.parametrize("identifier", ["../input", "a"*31, "a"*32+"/..", "g"*32])
def test_job_ids_cannot_escape(identifier):
    with pytest.raises(AudioError):
        job_directory({"root": "E:/unused"}, identifier)


def test_word_conditioning_and_unicode_melisma_roundtrip(tmp_path):
    aligned = {"words": [{"start": .2, "end": 1.2, "text": "sa"}, {"start": 1.4, "end": 2.4, "text": "shi"}],
               "texts": [{"start": .2, "end": 1.2, "text": "さ"}, {"start": 1.4, "end": 2.4, "text": "し"}]}
    words = game_dataset(aligned, 2.6, tmp_path / "input.csv")
    assert [w["text"] for w in words] == ["SP", "sa", "SP", "shi", "SP"]
    output = tmp_path / "aligned.csv"
    with output.open("w", newline="", encoding="utf8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["note_seq", "note_dur", "note_slur"])
        writer.writeheader()
        writer.writerow({"note_seq": "rest C4+10 D4-20 rest E4 rest", "note_dur": ".2 .4 .6 .2 1 .2", "note_slur": "0 0 1 0 0 0"})
    notes = parse_game_aligned(output, words)
    assert [n["lyric"] for n in notes] == ["さ", "-", "し"]
    assert [n["word_index"] for n in notes] == [1, 1, 3]
    assert notes[0]["pitch"] == pytest.approx(60.1)
    assert notes[1]["pitch"] == pytest.approx(61.8)
    target = tmp_path / "score.mid"
    export_midi(notes, target)
    messages = list(mido.MidiFile(target, charset="utf8"))
    assert [m.text for m in messages if m.type == "lyrics"] == ["さ", "-", "し"]
    elapsed, onsets = 0.0, []
    for message in messages:
        elapsed += message.time
        if message.type == "note_on":
            onsets.append(elapsed)
    assert onsets == pytest.approx([.2, .6, 1.4])
    before = sha256(target)
    with pytest.raises(FileExistsError):
        export_midi(notes, target)
    assert sha256(target) == before


def test_alignment_keeps_distinct_phone_and_lyric_tiers(tmp_path):
    grid = textgrid.TextGrid(maxTime=2)
    for name, content in [("texts", [(0.2, 1.8, "山茶花")]),
                          ("words", [(.2, .6, "shan"), (.7, 1.1, "cha"), (1.2, 1.8, "hua")]),
                          ("phones", [(.2, .3, "sh"), (.3, .6, "an"), (.7, 1.1, "cha"), (1.2, 1.8, "hua")])]:
        tier = textgrid.IntervalTier(name=name, maxTime=2)
        for begin, end, label in content:
            tier.add(begin, end, label)
        grid.append(tier)
    source = tmp_path / "a.TextGrid"
    grid.write(str(source))
    result = alignment(source, 2)
    assert any(i["text"] == "山茶花" for i in result["texts"])
    assert any(i["text"] == "sh" for i in result["phones"])
    words = game_dataset(result, 2, tmp_path / "conditioned.csv", "zh")
    assert [w["lyric"] for w in words if w["text"] not in ("", "SP")] == ["山", "茶", "花"]


@pytest.mark.parametrize("notes", [[], [{"start": 0, "end": 0, "pitch": 60}],
                                   [{"start": 0, "end": 1, "pitch": float("nan")}],
                                   [{"start": 0, "end": 1, "pitch": 128}],
                                   [{"start": 0, "end": 2, "pitch": 60}, {"start": 1, "end": 3, "pitch": 62}]])
def test_invalid_acoustic_output_cannot_become_a_score(notes):
    with pytest.raises(AudioError):
        validate_notes(notes)


def test_cancellation_reaps_only_the_owned_model_process(tmp_path):
    worker = object.__new__(Worker)
    worker.folder = tmp_path
    worker.env = os.environ.copy()
    worker.update = lambda *a, **k: None
    errors = []
    child_identity = tmp_path / "child.pid"
    def run():
        try:
            worker.command([sys.executable, "-c", "import os,time,pathlib;pathlib.Path(" + repr(str(child_identity)) + ").write_text(str(os.getpid()));time.sleep(120)"], "model")
        except AudioError as error:
            errors.append(error.code)
    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 5
    while not child_identity.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert child_identity.exists()
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1001, False, int(child_identity.read_text()))
    assert handle
    (tmp_path / "cancel.requested").touch()
    thread.join(timeout=5)
    try:
        assert not thread.is_alive()
        assert errors == ["CANCELLED"]
        deadline = time.monotonic() + 3
        code = wintypes.DWORD(259)
        while code.value == 259 and time.monotonic() < deadline:
            api.GetExitCodeProcess(handle, ctypes.byref(code))
            time.sleep(.05)
        assert code.value != 259, "The real model survived cancellation of its venv launcher"
    finally:
        code = wintypes.DWORD(259)
        api.GetExitCodeProcess(handle, ctypes.byref(code))
        if code.value == 259:
            api.TerminateProcess(handle, 1)
        api.CloseHandle(handle)
