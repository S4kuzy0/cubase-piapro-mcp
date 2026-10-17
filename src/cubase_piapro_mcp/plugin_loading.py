"""Additive plugin loading with actual host identities and verified recovery."""

from __future__ import annotations

import json
import time
import uuid

from . import cubase, host_controls as hc, live_midi, plugin_catalog, plugin_ui, windows
from .errors import IntegrationError
from .storage import file_lock, state_dir, write_result


def _all_controls():
    return {group: hc.read_selected(group) for group in hc.GROUPS}


def _state(state):
    return {key: value for key, value in state.items() if key not in ("revision", "driver_revision")}


def verify_insert(before, after, slot):
    for group in hc.GROUPS:
        previous, current = _state(before[group]), _state(after[group])
        previous["plugins"] = {k: v for k, v in previous["plugins"].items() if k != slot}
        current["plugins"] = {k: v for k, v in current["plugins"].items() if k != slot}
        if group == slot:
            previous.pop("values"); current.pop("values")
            previous.pop("parameter_overflow"); current.pop("parameter_overflow")
        if previous != current:
            return False
    return True


def _identity(profile, actual):
    if profile["name"] != actual["name"]:
        return False
    if profile["vendor"] and profile["vendor"] != actual["vendor"]:
        return False
    if profile["version"] and profile["version"] != actual["version"]:
        return False
    family = lambda text: "VST3" if text.upper().replace(" ", "").startswith("VST3") else "VST2" if text.upper().replace(" ", "").startswith("VST2") else text
    return not profile["format"] or family(profile["format"]) == family(actual["format"])


def _checkpoint(action, before, profile):
    folder = state_dir() / "host-checkpoints" / ("plugin-" + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    checkpoint = write_result(None, str(folder / "before.local.json"),
        json.dumps({"action": action, "before": before, "plugin": profile}, ensure_ascii=False, indent=2).encode("utf-8"))
    return folder, checkpoint["path"]


def _stopped():
    state = live_midi.get_state()
    if not state.get("connected") or state["state"]["playing"] != 0 or state["state"]["recording"] != 0:
        raise IntegrationError("TRANSPORT_NOT_STOPPED", "Stop transport before plugin loading.")


def _selection_guard(inventory, track, expected_driver_revision):
    current = live_midi.list_tracks()
    if current["revision"] != inventory["revision"] or current["selected"]["uid"] != track["uid"]:
        raise IntegrationError("PLUGIN_TARGET_CHANGED", "Host track identity/selection changed before plugin input.")
    state = hc.read_selected("summary")
    hc._matches_target(state, inventory, track)
    if state["driver_revision"] != expected_driver_revision:
        raise IntegrationError("CONTROL_STATE_CHANGED", "Host controls changed while the plugin selector was open.")
    _stopped()


def _restore_insert(root, inventory, track, group, profile, before, folder):
    current = hc.read_selected("summary")
    hc._matches_target(current, inventory, track)
    actual = current["plugins"][group]
    if actual["name"]:
        if not _identity(profile, actual):
            return {"verified": False, "reason": "The slot no longer contains this transaction's plugin; nothing was removed."}
        refresh = lambda: _selection_guard(inventory, track, current["driver_revision"])
        plugin_ui.choose_insert(root, int(group[-1]), "No Effect", folder / ("recovery-selector-" + uuid.uuid4().hex), refresh, empty=False)
        plugin_ui.discard_new_plugin(root, track["name"], actual["name"], folder)
    after = _all_controls()
    return {"verified": all(_state(before[g]) == _state(after[g]) for g in hc.GROUPS)}


def _load_insert(profile, track_id, expected_revision, expected_control_revision, slot):
    if type(slot) is not int or not 0 <= slot < 4 or not track_id:
        raise IntegrationError("INVALID_PLUGIN_TARGET", "An explicit host track ID and insert slot zero through three are required.")
    group = "insert." + str(slot)
    with file_lock(state_dir() / "live-daw"):
        inventory, track = hc._target(track_id, expected_revision)
        _stopped()
        summary = hc.read_selected("summary")
        hc._matches_target(summary, inventory, track)
        if summary["revision"] != expected_control_revision:
            raise IntegrationError("CONTROL_STATE_CHANGED", "Read summary controls again before loading an insert.")
        if summary["automation_write"] != 0 or track["kind"] == "midi":
            raise IntegrationError("INVALID_PLUGIN_TARGET", "Use an audio/instrument channel with automation write off.")
        empty = [i for i in range(4) if not summary["plugins"]["insert." + str(i)]["name"]]
        if not empty or slot != empty[0]:
            raise IntegrationError("PLUGIN_SLOT_NOT_EMPTY", "Only the first empty insert among slots zero through three may be loaded. Existing plugins are never replaced.")
        before = _all_controls()
        root = cubase.project_root()
        folder, checkpoint = _checkpoint("load_empty_insert", before, profile)
        guard = hc.read_selected("summary")
        refresh = lambda: _selection_guard(inventory, track, guard["driver_revision"])
        try:
            method = plugin_ui.choose_insert(root, slot, profile["name"], folder / "selector", refresh)
            after = _all_controls()
            if not _identity(profile, after[group]["plugins"][group]) or not verify_insert(before, after, group):
                raise IntegrationError("PLUGIN_READBACK_MISMATCH", "Loaded identity or unrelated channel controls differ from the checkpoint.")
        except Exception as exc:
            try:
                recovery = _restore_insert(root, inventory, track, group, profile, before, folder)
            except Exception as recovery_error:
                recovery = {"verified": False, "reason": str(recovery_error)}
            raise IntegrationError("PLUGIN_LOAD_INCOMPLETE", json.dumps({"reason": str(exc), "checkpoint": checkpoint, "recovery": recovery}, ensure_ascii=False)) from exc
        return {"verified": True, "target": group, "track": track, "plugin": after[group]["plugins"][group],
                "controls": after[group], "checkpoint": checkpoint, "method": method,
                "disk_project_saved": False, "class_uid_verified": False}


def _track_signature(inventory):
    return [(t["uid"], t["name"], t["plugin"], t["kind"]) for t in inventory["tracks"]]


def _remove_added_track(before, added):
    current = live_midi.list_tracks()
    original = {t["uid"] for t in before["tracks"]}
    if (current["session"] != before["session"] or added["uid"] in original
            or _track_signature({"tracks": [t for t in current["tracks"] if t["uid"] != added["uid"]]}) != _track_signature(before)):
        return {"verified": False, "reason": "Unrelated track identities changed; nothing was removed."}
    live_midi.select_track(added["id"], current["revision"])
    root = cubase.project_root(); cubase._idle(root)
    windows.notify_activation(root["handle"])
    _, gui, _ = windows.api()
    items = windows._menu_items(root["handle"], gui.GetMenu(root["handle"]))
    matches = [x for x in items if x["path"] == "Project / Remove Selected Tracks" and x["enabled"]]
    if len(matches) != 1 or live_midi.list_tracks()["selected"]["uid"] != added["uid"]:
        return {"verified": False, "reason": "The newly created channel is not uniquely selected."}
    gui.PostMessage(root["handle"], 0x111, matches[0]["command"], 0)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        after = live_midi.list_tracks()
        if _track_signature(after) == _track_signature(before):
            selected = next((t for t in after["tracks"] if t["uid"] == before["selected"]["uid"]), None)
            if selected:
                live_midi.select_track(selected["id"], after["revision"])
            final = live_midi.list_tracks()
            return {"verified": _track_signature(final) == _track_signature(before)
                    and final["selected"]["uid"] == before["selected"]["uid"]}
        time.sleep(.1)
    return {"verified": False, "reason": "Track removal did not match the original live inventory."}


def _new_instrument(profile, expected_revision, name):
    with file_lock(state_dir() / "live-daw"):
        root = cubase.project_root(); cubase._idle(root); _stopped()
        before = live_midi.list_tracks()
        if before["revision"] != expected_revision:
            raise IntegrationError("TRACK_LIST_CHANGED", "Read current host tracks before adding an instrument.")
        if any(t["kind"] == "instrument" and t["plugin"] == profile["name"] for t in before["tracks"]):
            raise IntegrationError("INSTRUMENT_ALREADY_LOADED", "This adapter creates the first instrument instance per plugin name. Select the existing host UID; duplicate creation is not verified on this host.")
        previous_track = next((t for t in before["tracks"] if t["uid"] == before["selected"]["uid"]), None)
        previous_controls = _all_controls() if previous_track else None
        folder, checkpoint = _checkpoint("new_instrument", {"inventory": before, "selected_controls": previous_controls}, profile)
    # add_track owns its DAW lock and checks the same inventory revision again.
    try:
        added = cubase.add_track("instrument", name, expected_revision, True, plugin_name=profile["name"])
    except Exception as exc:
        with file_lock(state_dir() / "live-daw"):
            try:
                current = live_midi.list_tracks()
                original_ids = {t["uid"] for t in before["tracks"]}
                candidates = [t for t in current["tracks"] if t["uid"] not in original_ids]
                if len(candidates) == 1 and candidates[0]["name"] == name and candidates[0]["kind"] == "instrument":
                    recovery = _remove_added_track(before, candidates[0])
                else:
                    recovery = {"verified": _track_signature(current) == _track_signature(before),
                                "reason": "No uniquely identified added instrument was removed."}
                if recovery["verified"] and previous_controls:
                    restored = _all_controls()
                    recovery["verified"] = all(_state(previous_controls[g]) == _state(restored[g]) for g in hc.GROUPS)
            except Exception as recovery_error:
                recovery = {"verified": False, "reason": str(recovery_error)}
        raise IntegrationError("PLUGIN_LOAD_INCOMPLETE", json.dumps({"reason": str(exc), "checkpoint": checkpoint, "recovery": recovery}, ensure_ascii=False)) from exc
    with file_lock(state_dir() / "live-daw"):
        pending = windows.dialogs(root["pid"])
        if pending:
            return {"verified": False, "loaded_identity_verified": True, "initialization_required": True,
                    "track": added["track"], "dialogs": [w["title"] for w in pending], "checkpoint": checkpoint,
                    "next_step": "Resolve the plugin's own initialization dialog, then read its host controls. No dialog was automatically acknowledged."}
        try:
            controls = hc.read_selected("instrument")
            if controls["selected"]["uid"] != added["track"]["uid"] or not _identity(profile, controls["plugins"]["instrument"]):
                raise IntegrationError("PLUGIN_READBACK_MISMATCH", "The new channel does not report the requested plugin metadata.")
            if previous_controls:
                inventory = live_midi.list_tracks()
                live_midi.select_track(previous_track["id"], inventory["revision"])
                preserved = _all_controls()
                if any(_state(previous_controls[g]) != _state(preserved[g]) for g in hc.GROUPS):
                    raise IntegrationError("PLUGIN_READBACK_MISMATCH", "The previously selected channel's controls changed during the addition.")
                live_midi.select_track(added["track"]["id"], inventory["revision"])
                controls = hc.read_selected("instrument")
        except Exception as exc:
            try:
                recovery = _remove_added_track(before, added["track"])
                if recovery["verified"] and previous_controls:
                    restored = _all_controls()
                    recovery["verified"] = all(_state(previous_controls[g]) == _state(restored[g]) for g in hc.GROUPS)
            except Exception as recovery_error:
                recovery = {"verified": False, "reason": str(recovery_error)}
            raise IntegrationError("PLUGIN_LOAD_INCOMPLETE", json.dumps({"reason": str(exc), "checkpoint": checkpoint, "recovery": recovery}, ensure_ascii=False)) from exc
    return {"verified": True, "target": "new_instrument", "addition": added, "track": added["track"],
            "plugin": controls["plugins"]["instrument"], "controls": controls, "checkpoint": checkpoint,
            "disk_project_saved": False, "class_uid_verified": False}


def load_plugin(plugin_id, expected_catalog_revision, expected_revision, target,
                *, track_name=None, track_id=None, insert_slot=None, expected_control_revision=None,
                allow_dialog_fallback=False):
    profile = plugin_catalog.requested_plugin(plugin_catalog.list_plugins(), plugin_id, expected_catalog_revision)
    if not allow_dialog_fallback:
        raise IntegrationError("DIALOG_FALLBACK_REQUIRED", "Cubase 14 lacks the newer plugin assignment manager. Explicitly enable the bounded selector fallback.")
    if target == "new_instrument":
        if profile["kind"] != "instrument":
            raise IntegrationError("INVALID_PLUGIN_TARGET", "New instrument loading requires cached Instrument metadata.")
        if "piapro" in profile["name"].casefold():
            return cubase.load_piapro(expected_revision, track_name or "Piapro", True)
        return _new_instrument(profile, expected_revision, track_name or profile["name"][:80])
    if target == "insert":
        if profile["kind"] == "instrument":
            raise IntegrationError("INVALID_PLUGIN_TARGET", "An instrument cannot be placed in an effect insert.")
        return _load_insert(profile, track_id, expected_revision, expected_control_revision, insert_slot)
    raise IntegrationError("INVALID_PLUGIN_TARGET", "Choose new_instrument or insert. Replacing occupied slots is not supported.")
