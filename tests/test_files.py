import io
import os
from pathlib import Path

import mido
import pytest
from lxml import etree as ET

from cubase_piapro_mcp import projects, ppsf, vsqx, midi, storage
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def score(tmp_path):
    data = (Path(vsqx.__file__).parent / "assets/blank.vsqx").read_bytes()
    tree, _ = vsqx.load(data)
    root = tree.getroot()
    extra = ET.SubElement(root, "{urn:custom}vendor-extra")
    extra.text = "keep this opaque extension"
    part = next(vsqx.parts(tree))[3]
    note = vsqx.local(part, "note")[0]
    note.find("{*}y").text = ET.CDATA("あ")
    file = tmp_path / "source.vsqx"
    file.write_bytes(vsqx.dump(tree))
    return file


def test_edit_preserves_voice_style_opaque_nodes_and_original(score, tmp_path):
    original = score.read_bytes()
    info = projects.inspect_project(str(score), True)
    note = info["notes"][0]
    result = projects.edit_notes(
        str(score),
        str(tmp_path / "changed.vsqx"),
        [{"id": note["id"], "lyric": "こんにちは", "note_num": 72}],
        info["sha256"],
    )
    changed = Path(result["path"]).read_bytes()
    before, _ = vsqx.load(original)
    after, _ = vsqx.load(changed)
    assert score.read_bytes() == original
    assert (
        after.getroot().find("{urn:custom}vendor-extra").text
        == "keep this opaque extension"
    )
    assert ET.tostring(before.getroot().find("{*}vVoiceTable")) == ET.tostring(
        after.getroot().find("{*}vVoiceTable")
    )
    assert vsqx.inspect(changed, True)["notes"][0]["lyric"] == "こんにちは"


def test_hash_conflict_never_creates_output(score, tmp_path):
    target = tmp_path / "must-not-exist.vsqx"
    with pytest.raises(IntegrationError, match="FILE_CHANGED"):
        projects.edit_notes(
            str(score), str(target), [{"id": "t0:p0:n0", "note_num": 60}], "0" * 64
        )
    assert not target.exists()


def test_overwrite_has_readable_backup_and_restore(score):
    original = score.read_bytes()
    info = projects.inspect_project(str(score), True)
    result = projects.edit_notes(
        str(score), str(score), [{"id": "t0:p0:n0", "note_num": 60}], info["sha256"]
    )
    assert Path(result["backup"]["backup_path"]).read_bytes() == original
    restored = storage.restore_backup(
        result["backup"]["backup_path"], str(score), result["sha256"]
    )
    assert score.read_bytes() == original
    assert restored["backup"] is not None


def test_existing_unrelated_target_is_not_overwritten(score, tmp_path):
    target = tmp_path / "existing.vsqx"
    target.write_bytes(b"unique user content")
    info = projects.inspect_project(str(score), True)
    with pytest.raises(IntegrationError, match="TARGET_EXISTS"):
        projects.edit_notes(
            str(score),
            str(target),
            [{"id": "t0:p0:n0", "velocity": 80}],
            info["sha256"],
        )
    assert target.read_bytes() == b"unique user content"


def test_curve_keeps_other_controllers_and_note_styles(score):
    data = score.read_bytes()
    changed = vsqx.curve(data, 0, 0, "PBS", [{"tick": 0, "value": 12}])
    changed = vsqx.curve(changed, 0, 0, "PIT", [{"tick": 100, "value": 1024}])
    tree, _ = vsqx.load(changed)
    part = next(vsqx.parts(tree))[3]
    assert any(
        vsqx.text(node, "v") == "12" and node.find("{*}v").get("id") == "S"
        for node in vsqx.local(part, "cc")
    )
    assert vsqx.inspect(changed, True)["notes"] == vsqx.inspect(data, True)["notes"]
    with pytest.raises(IntegrationError):
        vsqx.curve(data, 0, 0, "PIT", [{"tick": 0, "value": 9000}])


def test_insert_delete_and_create_do_not_keep_template_note(score):
    data = score.read_bytes()
    entry = {
        "start_tick": 240,
        "duration_tick": 480,
        "note_num": 69,
        "velocity": 64,
        "lyric": "あ",
        "phoneme": "a",
    }
    created = vsqx.create(data, [entry], 0, 0, 120, "Test vocal")
    assert len(vsqx.inspect(created, True)["notes"]) == 1
    added = vsqx.add_notes(
        created, 0, 0, [entry | {"start_tick": 1000, "lyric": "い", "phoneme": "i"}]
    )
    assert vsqx.inspect(added)["note_count"] == 2
    deleted = vsqx.delete_notes(added, ["t0:p0:n0"])
    assert vsqx.inspect(deleted, True)["notes"][0]["lyric"] == "い"


def test_vsqx3_and_external_entities():
    data = b"""<vsq3 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq3/"><vsTrack><trackName>T</trackName><musicalPart><note><posTick>0</posTick><durTick>480</durTick><noteNum>60</noteNum><velocity>64</velocity><lyric>a</lyric><phnms>a</phnms></note></musicalPart></vsTrack></vsq3>"""
    result = vsqx.edit(data, [{"id": "t0:p0:n0", "duration_tick": 960}])
    assert vsqx.inspect(result, True)["notes"][0]["duration_tick"] == 960
    with pytest.raises(IntegrationError, match="UNSUPPORTED_XML"):
        vsqx.load(
            b'<!DOCTYPE vsq4 [<!ENTITY x SYSTEM "file:///not-read">]><vsq4>&x;</vsq4>'
        )


def test_midi_pairing_channel_and_metadata_preserved():
    song = mido.MidiFile()
    track = mido.MidiTrack()
    song.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("set_tempo", tempo=600000),
            mido.Message("program_change", channel=2, program=5),
            mido.Message("note_on", channel=2, note=60, velocity=70),
            mido.Message("note_on", channel=3, note=60, velocity=90, time=120),
            mido.Message("note_off", channel=2, note=60, time=360),
            mido.Message("note_on", channel=3, note=60, velocity=0, time=120),
        ]
    )
    stream = io.BytesIO()
    song.save(file=stream)
    data = stream.getvalue()
    rows = midi.inspect(data, True)["notes"]
    changed = midi.edit(
        data,
        [{"id": rows[0]["id"], "note_num": 65, "start_tick": 60, "duration_tick": 600}],
    )
    output = mido.MidiFile(file=io.BytesIO(changed))
    notes = midi.inspect(changed, True)["notes"]
    edited = next(row for row in notes if row["channel"] == 2)
    assert (edited["note_num"], edited["start_tick"], edited["duration_tick"]) == (
        65,
        60,
        600,
    )
    assert next(m for m in output.tracks[0] if m.type == "set_tempo").tempo == 600000
    assert next(m for m in output.tracks[0] if m.type == "program_change").program == 5


@pytest.mark.skipif(
    not os.environ.get("CP_TEST_PPSF"),
    reason="Set CP_TEST_PPSF to a private local fixture; never commit user music.",
)
def test_real_legacy_ppsf_variable_length_edit_and_opaque_chunks():
    data = Path(os.environ["CP_TEST_PPSF"]).read_bytes()
    original = ppsf.inspect(data, True)
    assert original["binary_roundtrip_identical"]
    first = original["notes"][0]
    pitch_only = ppsf.edit(
        data, [{"id": first["id"], "note_num": first["note_num"] - 1}]
    )
    assert len(pitch_only) == len(data)
    assert sum(a != b for a, b in zip(data, pitch_only)) == 1
    changed = ppsf.edit(
        data,
        [
            {
                "id": first["id"],
                "lyric": first["lyric"] + "あいう",
                "note_num": first["note_num"] - 1,
            }
        ],
    )
    after = ppsf.inspect(changed, True)
    assert after["notes"][0]["lyric"] == first["lyric"] + "あいう"
    assert after["notes"][0]["note_num"] == first["note_num"] - 1
    assert after["note_count"] == original["note_count"]
    _, old = ppsf.load(data)
    _, new = ppsf.load(changed)
    for key in old:
        if key != "Events":
            assert old[key] == new[key]
    assert int.from_bytes(changed[4:8], "little") + 8 == len(changed)


@pytest.mark.skipif(
    not os.environ.get("CP_TEST_PPSF"), reason="Private local PPSF fixture required"
)
def test_native_timing_updates_redundant_editor_data():
    data = Path(os.environ["CP_TEST_PPSF"]).read_bytes()
    first = ppsf.inspect(data, True)["notes"][0]
    changed = ppsf.edit(
        data,
        [
            {
                "id": first["id"],
                "start_tick": first["start_tick"] + 1,
                "duration_tick": first["duration_tick"] - 1,
            }
        ],
    )
    _, chunks = ppsf.load(changed)
    metadata, _ = ppsf.editor_note_map(chunks)[int(first["id"][1:])]
    assert metadata.pos_tick == first["start_tick"] + 1
    assert metadata.dur_tick == metadata.dur_tick_copy == first["duration_tick"] - 1
    assert len(changed) == len(data)


def test_singer_and_curve_readback(score):
    data = score.read_bytes()
    updated = vsqx.set_singer(data, 0, 0, 0, 0)
    assert vsqx.inspect(updated)["parts"][0]["singers"][0]["program"] == 0
    with pytest.raises(IntegrationError, match="VOICE_NOT_FOUND"):
        vsqx.set_singer(data, 0, 0, 127, 127)
    changed = vsqx.curve(
        data, 0, 0, "PIT", [{"tick": 20, "value": -120}, {"tick": 50, "value": 240}]
    )
    assert vsqx.read_curve(changed, 0, 0, "PIT") == [
        {"tick": 20, "value": -120},
        {"tick": 50, "value": 240},
    ]


def test_vsqx_timing_edit_extends_part_and_orders_notes(score):
    data = score.read_bytes()
    first = vsqx.inspect(data, True)["notes"][0]
    changed = vsqx.edit(
        data, [{"id": first["id"], "start_tick": 500000, "duration_tick": 960}]
    )
    tree, fields = vsqx.load(changed)
    part = next(vsqx.parts(tree))[3]
    assert int(vsqx.text(part, "playTime")) >= 500960
    assert [
        int(vsqx.text(n, fields["start_tick"])) for n in vsqx.local(part, "note")
    ] == sorted(
        int(vsqx.text(n, fields["start_tick"])) for n in vsqx.local(part, "note")
    )


@pytest.mark.skipif(
    not os.environ.get("CP_TEST_PPSF"), reason="Private local PPSF fixture required"
)
def test_native_conflicting_duration_is_rejected():
    from cubase_piapro_mcp._vendor.libresvip_legacy import model

    data = Path(os.environ["CP_TEST_PPSF"]).read_bytes()
    project, chunks = ppsf.load(data)
    first = ppsf.inspect(data, True)["notes"][0]
    metadata, _ = ppsf.editor_note_map(chunks)[int(first["id"][1:])]
    metadata.dur_tick += 1
    conflicted = model.PpsfLegacyProject.build(project)
    with pytest.raises(IntegrationError, match="PPSF_TIMING_MAP_UNSUPPORTED"):
        ppsf.edit(
            conflicted,
            [{"id": first["id"], "duration_tick": first["duration_tick"] - 1}],
        )
