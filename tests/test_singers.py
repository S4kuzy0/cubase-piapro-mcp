import os
from copy import deepcopy
from pathlib import Path

import pytest

from cubase_piapro_mcp import ppsf, singers, storage
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def native():
    path = os.environ.get("CP_TEST_PPSF")
    if not path:
        pytest.skip("Set CP_TEST_PPSF to a local native Piapro fixture")
    return Path(path).read_bytes()


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))


def test_native_singer_changes_only_the_requested_program(native):
    before = singers.inspect_native(native)
    assert len(before["singers"]) >= 2
    _, chunks = ppsf.load(native)
    target = before["singers"][0]
    singer_bytes = ppsf.model.PpsfProjectV3SingerEntry.build(chunks["Project"].data.impl.vocaloid3_singers[0])
    changed = singers.assign_native(native, 0, singer_bytes)
    after = singers._verify_applied(native, changed, 0, target["id"])
    assert after["tracks"][0]["singer"]["id"] == target["id"]
    assert after["tracks"][1:] == before["tracks"][1:]
    assert ppsf.inspect(native, True)["notes"] == ppsf.inspect(changed, True)["notes"]
    # No project rebuild, program-index relocation, or other payload edits needed.
    assert len(native) == len(changed)
    assert sum(a != b for a, b in zip(native, changed)) == 2


def test_new_singer_entry_preserves_native_chunks_and_can_restore(native):
    _, chunks = ppsf.load(native)
    entry = deepcopy(chunks["Project"].data.impl.vocaloid3_singers[0])
    entry.singer_id = entry.singer_name = "Test imported singer with longer name"
    raw = ppsf.model.PpsfProjectV3SingerEntry.build(entry)
    result = singers.assign_native(native, 0, raw)
    after = singers._verify_applied(native, result, 0, "singer:" + storage.digest(raw))
    assert after["binary_roundtrip_identical"]
    assert len(after["singers"]) == len(chunks["Project"].data.impl.vocaloid3_singers) + 1
    original = ppsf.model.PpsfProjectV3SingerEntry.build(chunks["Project"].data.impl.vocaloid3_singers[1])
    restored = singers.assign_native(result, 0, original)
    assert singers.inspect_native(restored)["tracks"] == singers.inspect_native(native)["tracks"]


def test_unknown_timed_program_layout_and_shared_program_are_rejected(native):
    project, chunks = ppsf.load(native)
    track = chunks["Tracks"].data.vocaloid3_event_tracks.data[0]
    plugin, _ = singers.program_plugin(chunks, track)
    plugin.data.vfxc_blob.data = plugin.data.vfxc_blob.data + b"\0"
    with pytest.raises(IntegrationError, match="SINGER_LAYOUT_UNSUPPORTED"):
        singers.program_plugin(chunks, track)
    assert not singers.inspect_native(native)["tracks"][0].get("reason")
    project, chunks = ppsf.load(native)
    tracks = chunks["Tracks"].data.vocaloid3_event_tracks.data
    tracks[1].data.base.v3_event_plugin_indices[-1] = tracks[0].data.base.v3_event_plugin_indices[-1]
    shared = ppsf.model.PpsfLegacyProject.build(project)
    raw = ppsf.model.PpsfProjectV3SingerEntry.build(chunks["Project"].data.impl.vocaloid3_singers[0])
    with pytest.raises(IntegrationError, match="SINGER_LAYOUT_UNSUPPORTED"):
        singers.assign_native(shared, 0, raw)


def test_cache_checksum_and_source_hash_prevent_unintended_writes(native, tmp_path):
    singers.remember(native)
    selected = singers.catalog()[0]
    path = tmp_path / "score.ppsf"
    path.write_bytes(native)
    output = tmp_path / "must-not-exist.ppsf"
    with pytest.raises(IntegrationError, match="FILE_CHANGED"):
        singers.set_file(str(path), str(output), "0" * 64, 0, selected["id"])
    assert not output.exists()
    cached = storage.state_dir() / "singers" / (selected["id"][7:] + ".bin")
    cached.write_bytes(b"corrupt")
    with pytest.raises(IntegrationError, match="SINGER_CACHE_CHANGED"):
        singers.set_file(str(path), str(output), storage.digest(native), 0, selected["id"])
    assert not output.exists()


def test_cached_id_cannot_escape_catalog_directory():
    with pytest.raises(IntegrationError, match="INVALID_SINGER_ID"):
        singers.cached_entry("../../outside")


def test_readback_rejects_an_unrelated_synth_setting_change(native):
    before = singers.inspect_native(native)
    project, chunks = ppsf.load(native)
    plugin = chunks["Plugins"].data.vocaloid3_synth_event_control_plugins[0]
    raw = bytearray(plugin.data.data)
    raw[0] ^= 1
    plugin.data.data = bytes(raw)
    changed = ppsf.model.PpsfLegacyProject.build(project)
    with pytest.raises(IntegrationError, match="SINGER_READBACK_MISMATCH"):
        singers._verify_applied(native, changed, 0, before["tracks"][0]["singer"]["id"])


def test_live_change_rejects_stale_song_before_import(native, tmp_path, monkeypatch):
    from cubase_piapro_mcp import windows

    singers.remember(native)
    target = singers.inspect_native(native)["singers"][0]
    monkeypatch.setattr(singers, "installed_voicebanks", lambda: {"voicebanks": [
        {"component_id": target["component_id"]}, {"component_id": target["secondary_component_id"]}]})
    monkeypatch.setattr(singers, "_live_folder", lambda: tmp_path)
    monkeypatch.setattr(singers, "_capture", lambda folder, name: ({"path": "recovery.ppsf"}, native))
    monkeypatch.setattr(windows, "_send_file", lambda *a, **k: pytest.fail("No stale-song import allowed"))
    with pytest.raises(IntegrationError, match="PIAPRO_STATE_CHANGED"):
        singers.set_live(0, target["id"], "stale")


def test_live_readback_failure_restores_the_actual_snapshot(native, tmp_path, monkeypatch):
    from cubase_piapro_mcp import windows

    singers.remember(native)
    target = singers.inspect_native(native)["singers"][0]
    monkeypatch.setattr(singers, "installed_voicebanks", lambda: {"voicebanks": [
        {"component_id": target["component_id"]}, {"component_id": target["secondary_component_id"]}]})
    monkeypatch.setattr(singers, "_live_folder", lambda: tmp_path)
    captures = iter([({"path": "actual-before.ppsf"}, native),
                     ({"path": "wrong-readback.ppsf"}, native),
                     ({"path": "restored.ppsf"}, native)])
    monkeypatch.setattr(singers, "_capture", lambda folder, name: next(captures))
    imports = []
    monkeypatch.setattr(windows, "_send_file", lambda op, path, *a, **k: imports.append(path))
    with pytest.raises(IntegrationError, match="automatic restoration verified: True"):
        singers.set_live(0, target["id"], singers.revision(native))
    assert len(imports) == 2
    assert imports[-1] == "actual-before.ppsf"
