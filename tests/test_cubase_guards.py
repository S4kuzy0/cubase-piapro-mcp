import pytest

from cubase_piapro_mcp import cubase
from cubase_piapro_mcp.errors import IntegrationError


def track(uid, name, plugin=""):
    return {"id": uid, "uid": uid, "name": name, "plugin": plugin,
            "kind": "instrument" if plugin else "audio_or_sampler"}


def test_selector_waits_for_its_delayed_native_search_edit(monkeypatch):
    ready = {"handle": 12, "class": "Edit", "visible": True}
    states = iter([[], [], [ready]])
    monkeypatch.setattr(cubase.windows, "children", lambda hwnd: next(states))
    monkeypatch.setattr(cubase.time, "sleep", lambda duration: None)
    assert cubase._only_edit(34) == ready


def test_midi_route_to_existing_instrument_is_not_a_new_plugin_instance():
    original = track("owner", "Piapro", "Piapro Studio VSTi")
    routed = track("route", "MIDI", "Piapro Studio VSTi") | {"kind": "midi"}
    assert cubase._new_track({"tracks": [original]}, {"tracks": [original, routed]}, "MIDI", "midi") == routed
    with pytest.raises(IntegrationError, match="PIAPRO_NOT_LOADED"):
        cubase._new_track({"tracks": [original]}, {"tracks": [original, routed]}, "MIDI", "piapro")


def test_addition_requires_preserved_existing_ids_and_one_new_named_track():
    before = {"tracks": [track("old", "Original")]}
    after = {"tracks": [track("old", "Original"), track("new", "Voice", "Piapro Studio VSTi")]}
    assert cubase._new_track(before, after, "Voice", "piapro")["id"] == "new"
    with pytest.raises(IntegrationError, match="UNVERIFIED_TRACK_ADDITION"):
        cubase._new_track(before, {"tracks": after["tracks"][1:]}, "Voice", "piapro")
    after["tracks"].append(track("unexpected", "Voice", "Piapro Studio VSTi"))
    with pytest.raises(IntegrationError, match="UNVERIFIED_TRACK_ADDITION"):
        cubase._new_track(before, after, "Voice", "piapro")


def test_existing_piapro_instance_is_rejected_before_any_host_command(tmp_path, monkeypatch):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(cubase, "project_root", lambda: {"pid": 123, "handle": 456})
    monkeypatch.setattr(cubase, "_idle", lambda root: None)
    monkeypatch.setattr(cubase.live_midi, "list_tracks", lambda: {
        "revision": "current", "overflow": False, "tracks": [track("p", "Voice", "Piapro Studio VSTi")]})
    def forbidden_command(action):
        pytest.fail("A duplicate Piapro host command must never be sent")
    monkeypatch.setattr(cubase.live_midi, "command", forbidden_command)
    with pytest.raises(IntegrationError, match="PIAPRO_ALREADY_LOADED"):
        cubase.add_track("piapro", "Another Voice", "current", True)


def test_creation_does_not_close_an_existing_project_or_create_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(cubase, "project_windows", lambda: [{"title": "Existing project"}])
    target = tmp_path / "new-song"
    with pytest.raises(IntegrationError, match="PROJECT_ALREADY_OPEN"):
        cubase.create_project("piapro-empty", str(target))
    assert not target.exists()
