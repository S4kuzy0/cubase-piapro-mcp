"""Target-bound mixer/plugin controls through cached MIDI Remote host bindings."""

from __future__ import annotations

import json
import math
import random
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import mido

from . import cubase, live_midi
from .errors import IntegrationError
from .storage import digest, file_lock, state_dir, write_result

GROUPS = ("summary", "eq", "sends", "instrument", "insert.0", "insert.1", "insert.2", "insert.3")
TOLERANCE = 0.000003
# Only aliases observed in an actual host A/B are accepted as linked feedback.
# Equal parameter titles alone do not prove two controls address the same state.
BYPASS_ALIASES = {("RoomWorks SE", "Steinberg Media Technologies", "14.0.40.101", "VST 3.7.12"): 7,
                  ("Piapro Studio VSTi", "Crypton", "1.0.0.0", "VST 2.4"): 0}


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_frames(frames, total):
    if not 1 <= total <= 127 or set(frames) != set(range(total)):
        raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Incomplete control response.")
    raw = bytearray()
    for index in range(total):
        frame = frames[index]
        if len(frame) > 600 or len(frame) % 3:
            raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Invalid control frame size.")
        for offset in range(0, len(frame), 3):
            low, middle, high = frame[offset:offset + 3]
            if not 0 <= low < 128 or not 0 <= middle < 128 or not 0 <= high < 4:
                raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Invalid UTF-16 code unit.")
            raw.extend((low | middle << 7 | high << 14).to_bytes(2, "little"))
    try:
        result = json.loads(raw.decode("utf-16-le"))
    except (UnicodeError, ValueError) as exc:
        raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Invalid control JSON.") from exc
    if not isinstance(result, dict):
        raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Expected a control object.")
    return result


def _receive(incoming, nonce, timeout=3):
    frames, total = {}, None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in incoming.iter_pending():
            if message.type != "sysex" or tuple(message.data[:5]) != (125, 67, 80, 6, nonce):
                continue
            if len(message.data) < 7:
                raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Truncated control header.")
            index, count = message.data[5:7]
            if not 1 <= count <= 127 or index >= count or (total is not None and total != count):
                raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Inconsistent control frames.")
            frame = tuple(message.data[7:])
            if index in frames and frames[index] != frame:
                raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Conflicting duplicate control frame.")
            frames[index], total = frame, count
            if len(frames) == total:
                return decode_frames(frames, total)
        time.sleep(.01)
    raise IntegrationError("CONTROL_DRIVER_REQUIRED", "No controls-1 response. Install the matching driver while Cubase is closed, then open a disposable project. A timed-out write is never automatically re-sent.")


def _query(incoming, outgoing, group):
    if group not in GROUPS:
        raise IntegrationError("INVALID_CONTROL_GROUP", "Choose summary, eq, sends, instrument or insert.0 through insert.3.")
    for _ in incoming.iter_pending():
        pass
    live_midi.send_to_host(outgoing, mido.Message("control_change", channel=15, control=113, value=GROUPS.index(group)))
    live_midi.send_to_host(outgoing, mido.Message("control_change", channel=15, control=114, value=0))
    time.sleep(.02)
    nonce = random.randint(1, 126)
    live_midi.send_to_host(outgoing, mido.Message("control_change", channel=15, control=114, value=nonce))
    return _receive(incoming, nonce)


def normalize_snapshot(data, group):
    if data.get("error") == "CONTROL_RESPONSE_TOO_LARGE":
        raise IntegrationError("CONTROL_RESPONSE_TOO_LARGE", "The cached metadata exceeds this protocol's bounded response size; nothing was written.")
    if (data.get("build") != "controls-1" or data.get("group") != group
            or data.get("active") is not True or not isinstance(data.get("session"), str)
            or not data["session"] or type(data.get("revision")) is not int
            or not isinstance(data.get("selected"), dict)
            or not isinstance(data["selected"].get("uid"), str) or not data["selected"]["uid"]
            or not isinstance(data.get("plugins"), dict) or not isinstance(data.get("values"), list)
            or len(data["values"]) > 80 or type(data.get("quiet_ms")) not in (int, float)
            or not math.isfinite(data["quiet_ms"]) or data["quiet_ms"] < 0
            or data.get("automation_write") not in (None, 0, 1)):
        raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Unknown or inactive controls schema.")
    plugin_keys = ("instrument", "insert.0", "insert.1", "insert.2", "insert.3")
    if set(data["plugins"]) != set(plugin_keys):
        raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Unknown plugin slot inventory.")
    for plugin in data["plugins"].values():
        if any(not isinstance(plugin.get(key), str) or len(plugin[key]) > 256
               for key in ("name", "vendor", "version", "format", "title")):
            raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Invalid plugin identity metadata.")
    values, seen, overflow = [], set(), False
    patterns = {"summary": r"channel\.automation_write", "eq": r"eq\.[1-4]\.(gain|frequency|q|on|filter)",
                "sends": r"send\.[0-7]\.(level|on|pre)"}
    pattern = patterns.get(group, re.escape(group) + r"\.(on|bypass|automation_write|parameter\.([0-9]|[1-5][0-9]|6[0-4]))")
    for item in data["values"]:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not re.fullmatch(pattern, item["id"])
                or item["id"] in seen or item.get("group") != group
                or type(item.get("binary")) is not bool
                or any(not isinstance(item.get(key), str) or len(item[key]) > 256
                       for key in ("title", "object_title", "display", "units"))
                or (item.get("active") is not None and type(item["active"]) is not bool)
                or (item.get("value") is not None and (type(item["value"]) not in (int, float)
                    or not math.isfinite(item["value"]) or not 0 <= item["value"] <= 1))):
            raise IntegrationError("INVALID_CONTROL_FEEDBACK", "Invalid control entry.")
        seen.add(item["id"])
        if item["id"] == group + ".parameter.64":
            overflow = bool(item["title"])
            continue
        entry = {key: item[key] for key in ("id", "group", "binary", "title", "object_title", "display", "units", "value", "active")}
        entry["binary"] = entry["binary"] or entry["title"] == "Bypass"
        entry["writable"] = bool(entry["active"] and entry["title"] and entry["value"] is not None
                                 and ".automation_write" not in entry["id"] and not entry["title"].casefold().startswith("[program"))
        if group == "sends":
            slot = int(entry["id"].split(".")[1])
            destination = re.fullmatch(str(slot + 1) + r"\s*-\s*(\S.*)", entry["object_title"])
            entry["destination_label"] = destination.group(1) if destination else None
            entry["writable"] = entry["writable"] and destination is not None
        if group in data["plugins"] and not data["plugins"][group]["name"]:
            entry["writable"] = False
        values.append(entry)
    result = {key: data[key] for key in ("build", "group", "session", "selected", "plugins", "revision", "automation_write")}
    result["driver_revision"] = result.pop("revision")
    result["values"] = values
    result["parameter_overflow"] = overflow
    result["revision"] = digest(_json(result).encode("utf-8"))
    return result


def read_selected(group):
    source, target = live_midi.selected_ports()
    with (file_lock(state_dir() / "live-midi"), mido.open_input(source) as incoming,
          mido.open_output(target) as outgoing):
        previous = None
        for _ in range(12):
            data = _query(incoming, outgoing, group)
            current = normalize_snapshot(data, group)
            if previous and previous["revision"] == current["revision"] and data.get("quiet_ms", 0) >= 120:
                return current
            previous = current
            time.sleep(.08)
    raise IntegrationError("CONTROL_STATE_PENDING", "Host parameter feedback is still changing. No control write was sent.")


def _target(track_id, expected_revision):
    root = cubase.project_root()
    cubase._idle(root)
    inventory = live_midi.list_tracks()
    track = live_midi.requested_track(inventory, track_id, expected_revision)
    if not live_midi._selected_matches(inventory, track):
        live_midi.select_track(track_id, inventory["revision"])
    return inventory, track


def _matches_target(state, inventory, track):
    if state["session"] != inventory["session"] or state["selected"]["uid"] != track["uid"]:
        raise IntegrationError("CONTROL_TARGET_CHANGED", "The selected host channel changed. No write was authorized by this snapshot.")


def get_controls(track_id, expected_revision, group="summary"):
    if group not in GROUPS:
        raise IntegrationError("INVALID_CONTROL_GROUP", "Choose a supported control group before selecting a host channel.")
    with file_lock(state_dir() / "live-daw"):
        inventory, track = _target(track_id, expected_revision)
        result = read_selected(group)
        _matches_target(result, inventory, track)
        return result | {"host_revision": inventory["revision"], "track": track,
                         "source": "Cubase MIDI Remote cached host value callbacks",
                         "scope": "four EQ bands, installed send slots up to eight; instrument and first four inserts, first 64 public parameters per slot",
                         "plugin_identity_verification": "host name/vendor/version/format, not loaded VST class UID"}


def validate_changes(before, changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= 16:
        raise IntegrationError("INVALID_CONTROL_CHANGES", "Provide one to sixteen explicit parameter changes.")
    values = {item["id"]: item for item in before["values"]}
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"id", "value"}:
            raise IntegrationError("INVALID_CONTROL_CHANGES", "Each change needs only id and normalized value.")
        entry = values.get(change["id"]) if isinstance(change["id"], str) else None
        value = change["value"]
        if (not entry or not entry["writable"] or change["id"] in seen
                or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                or (entry["binary"] and value not in (0, 1))):
            raise IntegrationError("INVALID_CONTROL_CHANGES", "Use unique writable IDs from the latest control read; binary controls accept zero or one. Program selection is intentionally read-only.")
        seen.add(change["id"])
    linked = _linked_wanted(before, {item["id"]: item["value"] for item in changes})
    if linked is None:
        raise IntegrationError("INVALID_CONTROL_CHANGES", "Verified aliases of the same Bypass state cannot request different values.")
    return [change for change in changes if abs(values[change["id"]]["value"] - change["value"]) > TOLERANCE]


def _dispatch(before, changes):
    source, target = live_midi.selected_ports()
    request = {"operation_id": uuid.uuid4().hex, "session": before["session"],
               "uid": before["selected"]["uid"], "group": before["group"],
               "revision": before["driver_revision"], "changes": changes}
    raw = _json(request).encode("utf-16-le")
    payload = []
    for offset in range(0, len(raw), 2):
        code = int.from_bytes(raw[offset:offset + 2], "little")
        payload.extend((code & 127, (code >> 7) & 127, (code >> 14) & 3))
    nonce = random.randint(1, 126)
    with (file_lock(state_dir() / "live-midi"), mido.open_input(source) as incoming,
          mido.open_output(target) as outgoing):
        current = normalize_snapshot(_query(incoming, outgoing, before["group"]), before["group"])
        if current["revision"] != before["revision"]:
            raise IntegrationError("CONTROL_STATE_CHANGED", "Host values changed before dispatch; nothing was sent.")
        # Exactly once. Even a missing response must never trigger an automatic resend.
        try:
            live_midi.send_to_host(outgoing, mido.Message("sysex", data=[125, 67, 80, 7, nonce, 0, 1] + payload))
            return _receive(incoming, nonce, 5) | {"operation_id": request["operation_id"]}
        except Exception as exc:
            if isinstance(exc, IntegrationError) and exc.code in ("HOST_CRASH_DETECTED", "HOST_HEALTH_UNVERIFIED"):
                raise  # The send guard refused before the payload reached the output.
            raise IntegrationError("CONTROL_DISPATCH_UNKNOWN", "The request may have reached Cubase; it was not re-sent. " + str(exc)) from exc


def _linked_wanted(before, wanted):
    result = dict(wanted)
    group = before["group"]
    plugin = before["plugins"].get(group)
    if plugin is None:
        return result
    index = BYPASS_ALIASES.get(tuple(plugin[key] for key in ("name", "vendor", "version", "format")))
    if index is None:
        return result
    ids = (group + ".bypass", group + ".parameter." + str(index))
    entries = {item["id"]: item for item in before["values"]}
    if not all(key in entries and entries[key]["title"] == "Bypass" for key in ids):
        return result
    values = [wanted[key] for key in ids if key in wanted]
    if values:
        if any(value != values[0] for value in values):
            return None
        result.update({key: values[0] for key in ids})
    return result


def verify_change(before, after, changes):
    if any(before[key] != after[key] for key in ("session", "selected", "plugins", "group", "automation_write", "parameter_overflow")):
        return False
    wanted = _linked_wanted(before, {item["id"]: item["value"] for item in changes})
    if wanted is None:
        return False
    previous, current = ({item["id"]: item for item in state["values"]} for state in (before, after))
    if previous.keys() != current.keys():
        return False
    for key, original in previous.items():
        observed = current[key]
        if key in wanted:
            if observed["value"] is None or abs(observed["value"] - wanted[key]) > TOLERANCE:
                return False
            if any(original[field] != observed[field] for field in ("title", "object_title", "active", "writable", "binary")):
                return False
        elif original != observed:
            return False
    return True


def _recover(before, changes):
    current = read_selected(before["group"])
    if current["session"] != before["session"] or current["selected"] != before["selected"] or current["plugins"] != before["plugins"]:
        return {"verified": False, "reason": "Target/plugin identity changed; recovery was not dispatched."}
    original = {item["id"]: item for item in before["values"]}
    observed = {item["id"]: item for item in current["values"]}
    for item in changes:
        value = observed.get(item["id"], {}).get("value")
        if value is None or (abs(value - item["value"]) > TOLERANCE
                             and abs(value - original[item["id"]]["value"]) > TOLERANCE):
            return {"verified": False, "reason": "Current target value is neither the original nor the requested value; recovery was not dispatched."}
    restore = [{"id": item["id"], "value": original[item["id"]]["value"]} for item in changes]
    restore = validate_changes(current, restore)
    if restore:
        acknowledgement = _dispatch(current, restore)
        if acknowledgement.get("dispatched") is not True:
            return {"verified": False, "acknowledgement": acknowledgement}
    after = read_selected(before["group"])
    return {"verified": verify_change(before, after, []), "after_revision": after["revision"]}


def set_controls(track_id, expected_revision, expected_control_revision, group, changes):
    if group not in GROUPS:
        raise IntegrationError("INVALID_CONTROL_GROUP", "Choose a supported control group before selecting a host channel.")
    with file_lock(state_dir() / "live-daw"):
        inventory, track = _target(track_id, expected_revision)
        before = read_selected(group)
        _matches_target(before, inventory, track)
        if before["revision"] != expected_control_revision:
            raise IntegrationError("CONTROL_STATE_CHANGED", "Read current controls and use their revision; no parameter write was sent.")
        changes = validate_changes(before, changes)
        if not changes:
            return {"verified": True, "already_at_requested_value": True, "after": before,
                    "disk_project_saved": False, "interface": "MIDI Remote standard host value bindings"}
        state = live_midi.get_state()
        if not state.get("connected") or state["state"]["playing"] != 0 or state["state"]["recording"] != 0:
            raise IntegrationError("TRANSPORT_NOT_STOPPED", "Stop transport before changing verified host controls.")
        if before["automation_write"] != 0:
            raise IntegrationError("AUTOMATION_WRITE_ENABLED", "Turn off channel automation write before a parameter transaction.")
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
        checkpoint = write_result(None, str(state_dir() / "host-checkpoints" / (stamp + ".controls.local.json")),
                                  _json({"before": before, "changes": changes, "track": track}).encode("utf-8"))
        try:
            acknowledgement = _dispatch(before, changes)
        except IntegrationError as exc:
            if exc.code != "CONTROL_DISPATCH_UNKNOWN":
                raise
            # A timeout can follow dispatch. Read actual state; recovery is never called success without rereading.
            try:
                recovery = _recover(before, changes)
            except Exception as recovery_error:
                recovery = {"verified": False, "reason": str(recovery_error)}
            raise IntegrationError("CONTROL_OPERATION_INCOMPLETE", _json({"reason": str(exc), "checkpoint": checkpoint["path"], "recovery": recovery})) from exc
        if acknowledgement.get("dispatched") is False:
            raise IntegrationError(acknowledgement.get("code", "CONTROL_OUTCOME_UNKNOWN"), _json({"acknowledgement": acknowledgement, "checkpoint": checkpoint["path"]}))
        try:
            if acknowledgement.get("dispatched") is not True:
                raise IntegrationError("CONTROL_OUTCOME_UNKNOWN", _json(acknowledgement))
            after = read_selected(group)
            if not verify_change(before, after, changes):
                raise IntegrationError("CONTROL_READBACK_MISMATCH", "Requested values or unrelated parameters did not match the host readback.")
        except Exception as exc:
            try:
                recovery = _recover(before, changes)
            except Exception as recovery_error:
                recovery = {"verified": False, "reason": str(recovery_error)}
            raise IntegrationError("CONTROL_OPERATION_INCOMPLETE", _json({"reason": str(exc), "checkpoint": checkpoint["path"], "recovery": recovery})) from exc
        return {"verified": True, "before": before, "after": after, "changes": changes,
                "linked_feedback_ids": sorted(set(_linked_wanted(before, {item['id']: item['value'] for item in changes})) - {item['id'] for item in changes}),
                "acknowledgement": acknowledgement, "checkpoint": checkpoint["path"],
                "disk_project_saved": False, "interface": "MIDI Remote standard host value bindings"}
