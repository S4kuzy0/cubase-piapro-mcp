import copy
import json

import pytest

from cubase_piapro_mcp import host_controls as hc
from cubase_piapro_mcp.errors import IntegrationError
from test_live_tracks import frames_for


def raw_snapshot():
    plugins = {key: {"name": "", "vendor": "", "version": "", "format": "", "title": ""}
               for key in ("instrument", "insert.0", "insert.1", "insert.2", "insert.3")}
    return {"build": "controls-1", "session": "instance", "active": True, "group": "eq",
            "revision": 1, "quiet_ms": 200, "automation_write": 0,
            "selected": {"uid": "a", "name": "人声 🐳"}, "plugins": plugins,
            "values": [{"id": "eq.2.gain", "group": "eq", "binary": False, "title": "Gain",
                        "object_title": "EQ", "display": "0.0dB", "units": "", "value": .5,
                        "active": True, "feedback": 1},
                       {"id": "eq.2.on", "group": "eq", "binary": True, "title": "On",
                        "object_title": "EQ", "display": "Off", "units": "", "value": 0,
                        "active": True, "feedback": 1}]}


def normalized():
    return hc.normalize_snapshot(raw_snapshot(), "eq")


def test_control_unicode_frames_and_partial_messages():
    data = raw_snapshot()
    assert hc.decode_frames(*frames_for(data)) == data
    frames, total = frames_for(data)
    frames.pop(0)
    with pytest.raises(IntegrationError, match="INVALID_CONTROL_FEEDBACK"):
        hc.decode_frames(frames, total)
    with pytest.raises(IntegrationError, match="INVALID_CONTROL_FEEDBACK"):
        hc.decode_frames({0: (0, 0, 4)}, 1)


@pytest.mark.parametrize("mutation", [
    lambda d: d["values"].append(d["values"][0]),
    lambda d: d["values"][0].update(value=float("nan")),
    lambda d: d["values"][0].update(id="eq.8.gain"),
    lambda d: d.update(active=False),
    lambda d: d.update(quiet_ms="unknown"),
])
def test_malformed_host_states_are_never_actionable(mutation):
    data = raw_snapshot()
    mutation(data)
    with pytest.raises(IntegrationError, match="INVALID_CONTROL_FEEDBACK"):
        hc.normalize_snapshot(data, "eq")


def test_revision_ignores_callback_counts_but_binds_plugin_identity():
    data = raw_snapshot()
    before = hc.normalize_snapshot(data, "eq")
    data["quiet_ms"] = 600
    data["values"][0]["feedback"] += 5
    assert hc.normalize_snapshot(data, "eq")["revision"] == before["revision"]
    data["plugins"]["instrument"]["name"] = "Another synth"
    assert hc.normalize_snapshot(data, "eq")["revision"] != before["revision"]


def test_changes_require_unique_writable_ids_and_valid_binary_values():
    before = normalized()
    assert hc.validate_changes(before, [{"id": "eq.2.gain", "value": .5}]) == []
    for changes in ([{"id": "eq.2.on", "value": .5}], [{"id": "eq.2.gain", "value": True}],
                    [{"id": "eq.2.gain", "value": .6}] * 2, [{"id": [], "value": .5}]):
        with pytest.raises(IntegrationError, match="INVALID_CONTROL_CHANGES"):
            hc.validate_changes(before, changes)


def test_send_destination_labels_are_real_host_metadata_and_empty_sends_are_read_only():
    data = raw_snapshot()
    data["group"] = "sends"
    data["values"] = [data["values"][0] | {"id": "send.0.level", "group": "sends", "title": "Level", "object_title": "1- Stereo Out"},
                      data["values"][1] | {"id": "send.1.on", "group": "sends", "title": "Enable", "object_title": "2"}]
    result = hc.normalize_snapshot(data, "sends")
    assert result["values"][0]["destination_label"] == "Stereo Out"
    assert result["values"][0]["writable"]
    assert not result["values"][1]["writable"]
    with pytest.raises(IntegrationError, match="INVALID_CONTROL_CHANGES"):
        hc.validate_changes(result, [{"id": "send.1.on", "value": 1}])


def test_unrequested_value_changes_fail_complete_readback():
    before, after = normalized(), normalized()
    after["values"][0]["value"] = .75
    changes = [{"id": "eq.2.gain", "value": .75}]
    assert hc.verify_change(before, after, changes)
    after["values"][1]["value"] = 1
    assert not hc.verify_change(before, after, changes)


@pytest.mark.parametrize("group,name,vendor,version,format_name,index", [
    ("insert.0", "RoomWorks SE", "Steinberg Media Technologies", "14.0.40.101", "VST 3.7.12", 7),
    ("instrument", "Piapro Studio VSTi", "Crypton", "1.0.0.0", "VST 2.4", 0),
])
def test_verified_bypass_alias_and_unknown_plugin_refusal(group, name, vendor, version, format_name, index):
    before = normalized()
    before["group"] = group
    before["plugins"][group] = {"name": name, "vendor": vendor,
        "version": version, "format": format_name, "title": "1"}
    before["values"] = [before["values"][1] | {"id": identifier, "group": group, "title": "Bypass"}
                        for identifier in (group + ".bypass", group + ".parameter." + str(index))]
    after = copy.deepcopy(before)
    for item in after["values"]:
        item.update(value=1, display="On")
    changes = [{"id": group + ".bypass", "value": 1}]
    assert hc.verify_change(before, after, changes)
    before["plugins"][group]["name"] = after["plugins"][group]["name"] = "Unknown plugin"
    assert not hc.verify_change(before, after, changes)


def transaction_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))
    before = normalized()
    monkeypatch.setattr(hc, "_target", lambda *args: ({"session": "instance"}, {"uid": "a"}))
    monkeypatch.setattr(hc, "read_selected", lambda group: copy.deepcopy(before))
    monkeypatch.setattr(hc.live_midi, "get_state", lambda: {"connected": True, "state": {"playing": 0, "recording": 0}})
    return before


def test_stale_revision_never_dispatches_or_recovers(monkeypatch, tmp_path):
    transaction_env(monkeypatch, tmp_path)
    monkeypatch.setattr(hc, "_dispatch", lambda *args: pytest.fail("Must not dispatch"))
    monkeypatch.setattr(hc, "_recover", lambda *args: pytest.fail("Must not recover"))
    with pytest.raises(IntegrationError, match="CONTROL_STATE_CHANGED"):
        hc.set_controls("uid:a", "host", "stale", "eq", [{"id": "eq.2.gain", "value": .75}])


def test_predispatch_race_never_rolls_back_an_external_change(monkeypatch, tmp_path):
    before = transaction_env(monkeypatch, tmp_path)
    def stale(*args):
        raise IntegrationError("CONTROL_STATE_CHANGED", "Another writer changed the host before dispatch")
    monkeypatch.setattr(hc, "_dispatch", stale)
    monkeypatch.setattr(hc, "_recover", lambda *args: pytest.fail("No native request was sent"))
    with pytest.raises(IntegrationError, match="CONTROL_STATE_CHANGED"):
        hc.set_controls("uid:a", "host", before["revision"], "eq", [{"id": "eq.2.gain", "value": .75}])


def test_postwrite_failure_recovers_and_independently_verifies(monkeypatch, tmp_path):
    before = transaction_env(monkeypatch, tmp_path)
    current = copy.deepcopy(before)
    dispatched = []
    def dispatch(state, changes):
        dispatched.append(copy.deepcopy(changes))
        for change in changes:
            entry = next(x for x in current["values"] if x["id"] == change["id"])
            entry["value"] = change["value"]
        if len(dispatched) == 1:
            raise IntegrationError("CONTROL_DISPATCH_UNKNOWN", "Injected after actual modification")
        return {"dispatched": True}
    monkeypatch.setattr(hc, "_dispatch", dispatch)
    monkeypatch.setattr(hc, "read_selected", lambda group: copy.deepcopy(current))
    with pytest.raises(IntegrationError, match="CONTROL_OPERATION_INCOMPLETE") as caught:
        hc.set_controls("uid:a", "host", before["revision"], "eq", [{"id": "eq.2.gain", "value": .75}])
    assert '"verified":true' in str(caught.value)
    assert current == before
    assert len(dispatched) == 2
    checkpoint = next((tmp_path / "state" / "host-checkpoints").glob("*.json"))
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["before"] == before


def test_recovery_refuses_changed_identity_or_unrelated_target_value(monkeypatch):
    before = normalized()
    current = copy.deepcopy(before)
    monkeypatch.setattr(hc, "read_selected", lambda group: current)
    monkeypatch.setattr(hc, "_dispatch", lambda *args: pytest.fail("Must not overwrite an external change"))
    current["selected"]["uid"] = "another"
    assert not hc._recover(before, [{"id": "eq.2.gain", "value": .75}])["verified"]
    current["selected"]["uid"] = "a"
    current["values"][0]["value"] = .9
    assert not hc._recover(before, [{"id": "eq.2.gain", "value": .75}])["verified"]
