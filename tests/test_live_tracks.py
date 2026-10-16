import json

import pytest

from cubase_piapro_mcp import live_midi
from cubase_piapro_mcp.errors import IntegrationError


def frames_for(snapshot):
    raw = json.dumps(snapshot, ensure_ascii=False).encode("utf-16-le")
    payload = []
    for index in range(0, len(raw), 2):
        value = int.from_bytes(raw[index:index + 2], "little")
        payload.extend((value & 127, value >> 7 & 127, value >> 14))
    parts = [tuple(payload[index:index + 600]) for index in range(0, len(payload), 600)]
    return {index: part for index, part in reversed(list(enumerate(parts)))}, len(parts)


def snapshot():
    return {"session": "test-instance", "capacity": 64, "overflow": False, "driver_build": "host-cache-1",
            "selected": {"name": "人声 🐳", "uid": "a", "plugin": "Piapro Studio VSTi", "editor_open": 0},
            "tracks": [{"slot": 0, "name": "人声 🐳", "uid": "a", "kind": "instrument", "selected": True, "plugin": "Piapro Studio VSTi"},
                       {"slot": 1, "name": "人声 🐳", "uid": "b", "kind": "midi", "selected": False, "plugin": "Piapro Studio VSTi"}]}


def test_unicode_out_of_order_snapshot_and_duplicate_names():
    decoded = live_midi.decode_inventory(*frames_for(snapshot()))
    assert decoded["selected"]["name"] == "人声 🐳"
    assert [track["id"] for track in decoded["tracks"]] == ["uid:a", "uid:b"]
    target = live_midi.requested_track(decoded, "uid:b", decoded["revision"])
    assert not live_midi._selected_matches(decoded, target)


def test_revision_ignores_selection_but_rejects_track_reorder():
    original = snapshot()
    first = live_midi.decode_inventory(*frames_for(original))
    original["tracks"][0]["selected"] = False
    original["tracks"][1]["selected"] = True
    selected = live_midi.decode_inventory(*frames_for(original))
    assert selected["revision"] == first["revision"]
    original["tracks"][0]["slot"], original["tracks"][1]["slot"] = 1, 0
    reordered = live_midi.decode_inventory(*frames_for(original))
    with pytest.raises(IntegrationError, match="TRACK_LIST_CHANGED"):
        live_midi.requested_track(reordered, "uid:a", first["revision"])


def test_incomplete_corrupt_or_ambiguous_host_feedback_is_rejected():
    frames, total = frames_for(snapshot())
    with pytest.raises(IntegrationError, match="INVALID_TRACK_FEEDBACK"):
        live_midi.decode_inventory({}, total)
    frames[0] = (127, 127, 4)
    with pytest.raises(IntegrationError, match="INVALID_TRACK_FEEDBACK"):
        live_midi.decode_inventory(frames, total)
    ambiguous = snapshot()
    ambiguous["tracks"][1]["uid"] = "a"
    with pytest.raises(IntegrationError, match="INVALID_TRACK_FEEDBACK"):
        live_midi.decode_inventory(*frames_for(ambiguous))


def test_legacy_driver_never_receives_an_inventory_or_reset_request(monkeypatch):
    class Input:
        def iter_pending(self):
            return iter(())
    class Output:
        sent = []
        def send(self, message):
            self.sent.append(message)
    clock = iter(range(20))
    monkeypatch.setattr(live_midi.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(live_midi.time, "sleep", lambda value: None)
    outgoing = Output()
    with pytest.raises(IntegrationError, match="TRACK_DRIVER_REQUIRED"):
        live_midi._inventory(Input(), outgoing, reset_bank=True)
    assert {message.control for message in outgoing.sent} == {116}


def test_inactive_mapping_is_explicit_error_after_safe_handshake(monkeypatch):
    import mido
    pending = []
    class Input:
        def iter_pending(self):
            messages, pending[:] = list(pending), []
            return iter(messages)
    class Output:
        def send(self, message):
            if message.value and message.control in (116, 118):
                version = 5 if message.control == 116 else 4
                pending.append(mido.Message("sysex", data=[125, 67, 80, version, message.value, 1]))
    monkeypatch.setattr(live_midi.time, "sleep", lambda value: None)
    with pytest.raises(IntegrationError, match="NO_ACTIVE_PROJECT"):
        live_midi._inventory(Input(), Output())
