"""Small request/feedback bridge through Cubase MIDI Remote."""

from __future__ import annotations

import random
import time
import json
import mido

from .errors import IntegrationError
from . import host_health
from .storage import digest, file_lock, state_dir

INPUT_NAME = "CP MCP From Cubase"
OUTPUT_NAME = "CP MCP To Cubase"
FIELDS = ("playing", "recording", "volume", "pan", "mute", "solo")
TRACK_CAPACITY = 64
COMMANDS = {"add_audio": 100, "add_piapro": 101, "add_instrument": 101, "add_midi": 102, "save": 104, "save_as": 105}


def ports():
    return {"inputs": mido.get_input_names(), "outputs": mido.get_output_names()}


def selected_ports():
    host_health.require_healthy()
    available = ports()
    inputs = [p for p in available["inputs"] if INPUT_NAME in p]
    outputs = [p for p in available["outputs"] if OUTPUT_NAME in p]
    if len(inputs) != 1 or len(outputs) != 1:
        raise IntegrationError(
            "MIDI_SETUP_REQUIRED",
            f"Create the two named loopback MIDI ports and load the Cubase driver: {INPUT_NAME}; {OUTPUT_NAME}.",
        )
    return inputs[0], outputs[0]


def send_to_host(outgoing, message):
    host_health.require_healthy()
    outgoing.send(message)


def _query(incoming, outgoing, timeout=1.5):
    nonce = random.randint(1, 126)
    for _ in incoming.iter_pending():
        pass
    send_to_host(outgoing, mido.Message("control_change", channel=15, control=119, value=0))
    time.sleep(0.02)
    send_to_host(outgoing, mido.Message("control_change", channel=15, control=119, value=nonce))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in incoming.iter_pending():
            if (
                message.type == "sysex"
                and len(message.data) == 11
                and tuple(message.data[:5]) == (125, 67, 80, 2, nonce)
            ):
                return {
                    name: (None if value == 127 else value / 126)
                    for name, value in zip(FIELDS, message.data[5:])
                }
        time.sleep(0.02)
    raise IntegrationError(
        "NO_CUBASE_FEEDBACK",
        "The MIDI driver did not answer. Reload its script or check the port mappings.",
    )


def get_state():
    try:
        source, target = selected_ports()
        with (
            file_lock(state_dir() / "live-midi"),
            mido.open_input(source) as incoming,
            mido.open_output(target) as outgoing,
        ):
            values = _query(incoming, outgoing)
        return {
            "connected": True,
            "source": "Cubase MIDI Remote host value callbacks",
            "selected_track_only": True,
            "state": values,
        }
    except IntegrationError as exc:
        return {
            "connected": False,
            "code": exc.code,
            "reason": str(exc),
            "available_ports": ports(),
        }


def control(action: str, value: float | None = None):
    commands = {
        "play": (20, 1.0, "playing"),
        "stop": (21, 0.0, "playing"),
        "record": (22, 1.0, "recording"),
        "volume": (30, value, "volume"),
        "pan": (31, value, "pan"),
        "mute": (32, value, "mute"),
        "solo": (33, value, "solo"),
    }
    if action not in commands:
        raise IntegrationError("UNSUPPORTED_MIDI_ACTION", action)
    cc, wanted, field = commands[action]
    if wanted is None or not 0 <= wanted <= 1:
        raise IntegrationError(
            "INVALID_VALUE", "Mixer values use the normalized range from 0 to 1."
        )
    if action in ("mute", "solo") and wanted not in (0, 1):
        raise IntegrationError("INVALID_VALUE", "Mute and solo accept only 0 or 1.")
    source, target = selected_ports()
    with (
        file_lock(state_dir() / "live-midi"),
        mido.open_input(source) as incoming,
        mido.open_output(target) as outgoing,
    ):
        before = _query(incoming, outgoing)
        if before[field] is not None and abs(before[field] - wanted) <= 1 / 126:
            return {
                "verified": True,
                "action": action,
                "already_at_requested_value": True,
                "before": before,
                "after": before,
                "selected_track_only": True,
            }
        wire = 127 if action in ("play", "stop", "record") else round(wanted * 127)
        send_to_host(outgoing,
            mido.Message("control_change", channel=15, control=cc, value=wire)
        )
        if action in ("play", "stop", "record"):
            time.sleep(0.05)
            send_to_host(outgoing,
                mido.Message("control_change", channel=15, control=cc, value=0)
            )
        stable = 0
        for _ in range(12):
            time.sleep(0.08)
            after = _query(incoming, outgoing)
            observed = after[field]
            stable = (
                stable + 1
                if observed is not None and abs(observed - wanted) <= 1 / 126
                else 0
            )
            if stable >= 3:
                return {
                    "verified": True,
                    "action": action,
                    "before": before,
                    "after": after,
                    "selected_track_only": True,
                }
    raise IntegrationError(
        "UNVERIFIED_LIVE_CHANGE",
        "Cubase did not return the requested host value; the command may have executed. Inspect current state before retrying.",
    )


def _pulse(outgoing, cc, channel=15):
    send_to_host(outgoing, mido.Message("control_change", channel=channel, control=cc, value=0))
    time.sleep(0.02)
    send_to_host(outgoing, mido.Message("control_change", channel=channel, control=cc, value=127))
    time.sleep(0.05)
    send_to_host(outgoing, mido.Message("control_change", channel=channel, control=cc, value=0))


def decode_inventory(frames: dict[int, tuple[int, ...]], total: int) -> dict:
    """Decode ordered, complete UTF-16 frames; malformed host data is never actionable."""
    if not 1 <= total <= 127 or set(frames) != set(range(total)):
        raise IntegrationError("INVALID_TRACK_FEEDBACK", "Incomplete track snapshot.")
    raw = bytearray()
    for index in range(total):
        frame = frames[index]
        if len(frame) > 600 or len(frame) % 3:
            raise IntegrationError("INVALID_TRACK_FEEDBACK", "Invalid frame size.")
        for offset in range(0, len(frame), 3):
            low, middle, high = frame[offset:offset + 3]
            if not 0 <= low < 128 or not 0 <= middle < 128 or not 0 <= high < 4:
                raise IntegrationError("INVALID_TRACK_FEEDBACK", "Invalid UTF-16 code unit.")
            raw.extend((low | middle << 7 | high << 14).to_bytes(2, "little"))
    try:
        result = json.loads(raw.decode("utf-16-le"))
    except (UnicodeError, ValueError) as exc:
        raise IntegrationError("INVALID_TRACK_FEEDBACK", "Invalid snapshot encoding.") from exc
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("session"), str)
        or not result["session"]
        or result.get("capacity") != TRACK_CAPACITY
        or result.get("driver_build") != "host-cache-1"
        or type(result.get("generation", 0)) is not int or result.get("generation", 0) < 0
        or not isinstance(result.get("overflow"), bool)
        or not isinstance(result.get("selected"), dict)
        or not isinstance(result.get("tracks"), list)
        or len(result["tracks"]) > TRACK_CAPACITY
    ):
        raise IntegrationError("INVALID_TRACK_FEEDBACK", "Unsupported inventory schema.")
    slots, identifiers = set(), set()
    identity = []
    for track in result["tracks"]:
        if (
            not isinstance(track, dict)
            or type(track.get("slot")) is not int
            or not 0 <= track["slot"] < TRACK_CAPACITY
            or not isinstance(track.get("name"), str)
            or not track["name"]
            or not isinstance(track.get("uid"), str)
            or not isinstance(track.get("plugin"), str)
            or track.get("kind") not in ("instrument", "midi", "audio_or_sampler", "unknown")
            or type(track.get("selected")) is not bool
        ):
            raise IntegrationError("INVALID_TRACK_FEEDBACK", "Invalid track entry.")
        track["id"] = "uid:" + track["uid"] if track["uid"] else "slot:" + str(track["slot"])
        if track["slot"] in slots or track["id"] in identifiers:
            raise IntegrationError("INVALID_TRACK_FEEDBACK", "Duplicate track identity.")
        slots.add(track["slot"])
        identifiers.add(track["id"])
        identity.append({key: track[key] for key in ("slot", "name", "uid", "plugin", "kind")})
    selected = result["selected"]
    if any(not isinstance(selected.get(key), str) for key in ("name", "uid", "plugin")):
        raise IntegrationError("INVALID_TRACK_FEEDBACK", "Invalid selected-track metadata.")
    result["revision"] = digest(json.dumps(
        {"session": result["session"], "generation": result.get("generation", 0), "tracks": identity},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    result["scope"] = "first 64 audio/instrument/sampler/MIDI mixer channels; hidden channels included"
    result["source"] = "Cubase MIDI Remote host callbacks"
    return result


def _probe_inventory_driver(incoming, outgoing, timeout=.8):
    """Old protocol-3 drivers may crash on CC118: probe a separate safe CC first."""
    nonce = random.randint(1, 126)
    for _ in incoming.iter_pending():
        pass
    send_to_host(outgoing, mido.Message("control_change", channel=15, control=116, value=0))
    time.sleep(.02)
    send_to_host(outgoing, mido.Message("control_change", channel=15, control=116, value=nonce))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in incoming.iter_pending():
            if message.type == "sysex" and tuple(message.data[:5]) == (125, 67, 80, 5, nonce):
                if tuple(message.data[5:]) == (1,):
                    return
                raise IntegrationError("TRACK_DRIVER_REQUIRED", "Unsupported inventory build. Install/reload the host-cache-1 MIDI Remote driver before querying tracks.")
        time.sleep(.01)
    raise IntegrationError("TRACK_DRIVER_REQUIRED", "No host-cache-1 handshake. No track request or bank reset was sent. Open/activate a project; if it is already active, install the updated driver and use Scripting Tools > Reload Scripts, or restart Cubase after saving.")


def _inventory(incoming, outgoing, timeout=3.0, reset_bank=False):
    _probe_inventory_driver(incoming, outgoing)
    if reset_bank:
        _pulse(outgoing, 117)
        time.sleep(0.08)
    nonce = random.randint(1, 126)
    for _ in incoming.iter_pending():
        pass
    send_to_host(outgoing, mido.Message("control_change", channel=15, control=118, value=0))
    time.sleep(0.02)
    send_to_host(outgoing, mido.Message("control_change", channel=15, control=118, value=nonce))
    frames = {}
    total = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in incoming.iter_pending():
            if message.type == "sysex" and tuple(message.data[:5]) == (125, 67, 80, 4, nonce):
                raise IntegrationError("NO_ACTIVE_PROJECT", "The driver is loaded but no mapping page is active. Open/activate one project before querying tracks.")
            if message.type != "sysex" or tuple(message.data[:5]) != (125, 67, 80, 3, nonce):
                continue
            if len(message.data) < 7:
                raise IntegrationError("INVALID_TRACK_FEEDBACK", "Truncated frame header.")
            index, count = message.data[5:7]
            frame = tuple(message.data[7:])
            if not 1 <= count <= 127 or index >= count or (total is not None and total != count):
                raise IntegrationError("INVALID_TRACK_FEEDBACK", "Inconsistent frame header.")
            if index in frames and frames[index] != frame:
                raise IntegrationError("INVALID_TRACK_FEEDBACK", "Conflicting duplicate frame.")
            total = count
            frames[index] = frame
            if len(frames) == total:
                return decode_inventory(frames, total)
        time.sleep(0.01)
    raise IntegrationError("TRACK_DRIVER_REQUIRED", "No complete protocol-3 track snapshot. Install/reload the updated MIDI Remote driver.")


def list_tracks():
    source, target = selected_ports()
    with (
        file_lock(state_dir() / "live-midi"),
        mido.open_input(source) as incoming,
        mido.open_output(target) as outgoing,
    ):
        previous = _inventory(incoming, outgoing, reset_bank=True)
        for _ in range(5):
            time.sleep(.05)
            current = _inventory(incoming, outgoing)
            if current["revision"] == previous["revision"]:
                return current
            previous = current
        raise IntegrationError("TRACK_METADATA_PENDING", "Host track identities are still changing. Read the inventory again after the native operation settles.")


def requested_track(snapshot, track_id, expected_revision):
    if not expected_revision or expected_revision != snapshot["revision"]:
        raise IntegrationError("TRACK_LIST_CHANGED", "List live tracks again and use the returned revision; CPR indexes are not live IDs.")
    matches = [track for track in snapshot["tracks"] if track["id"] == track_id]
    if len(matches) != 1:
        raise IntegrationError("TRACK_NOT_FOUND", "The requested track is absent from the current snapshot.")
    if not matches[0]["uid"]:
        raise IntegrationError("TRACK_ID_PENDING", "The host has not returned a live UID. Read the inventory again; slot/name fallback cannot authorize selection.")
    return matches[0]


def _selected_matches(snapshot, track):
    if track["uid"] and snapshot["selected"]["uid"]:
        return snapshot["selected"]["uid"] == track["uid"]
    return snapshot["selected"]["name"] == track["name"] and any(
        item["slot"] == track["slot"] and item["selected"] for item in snapshot["tracks"]
    )


def select_track(track_id: str, expected_revision: str):
    source, target = selected_ports()
    with (
        file_lock(state_dir() / "live-midi"),
        mido.open_input(source) as incoming,
        mido.open_output(target) as outgoing,
    ):
        before = _inventory(incoming, outgoing, reset_bank=True)
        track = requested_track(before, track_id, expected_revision)
        if not _selected_matches(before, track):
            _pulse(outgoing, track["slot"], channel=14)
        stable = 0
        for _ in range(12):
            time.sleep(0.06)
            after = _inventory(incoming, outgoing)
            if after["revision"] != before["revision"]:
                raise IntegrationError("TRACK_LIST_CHANGED", "Track inventory changed during selection. Inspect current state before retrying.")
            stable = stable + 1 if _selected_matches(after, track) else 0
            if stable >= 2:
                return {"verified": True, "track": track, "revision": after["revision"], "selected": after["selected"], "method": "MIDI Remote host selection"}
    raise IntegrationError("UNVERIFIED_TRACK_SELECTION", "Cubase did not return the requested selected-track identity; inspect before retrying.")


def command(action: str):
    """Internal allowlist only. The caller must verify the resulting dialog or saved file."""
    if action not in COMMANDS:
        raise IntegrationError("UNSUPPORTED_HOST_COMMAND", action)
    source, target = selected_ports()
    with (
        file_lock(state_dir() / "live-midi"),
        mido.open_input(source) as incoming,
        mido.open_output(target) as outgoing,
    ):
        state = _query(incoming, outgoing)
        if state["playing"] != 0 or state["recording"] != 0:
            raise IntegrationError("TRANSPORT_NOT_STOPPED", "Verified stopped transport is required before project/track operations.")
        _inventory(incoming, outgoing)
        _pulse(outgoing, COMMANDS[action])
    return {"command_requested": action, "verified": False}


def open_instrument_editor(track_id: str, expected_revision: str):
    selected = select_track(track_id, expected_revision)
    source, target = selected_ports()
    with (
        file_lock(state_dir() / "live-midi"),
        mido.open_input(source) as incoming,
        mido.open_output(target) as outgoing,
    ):
        current = _inventory(incoming, outgoing)
        track = requested_track(current, track_id, expected_revision)
        if not _selected_matches(current, track):
            raise IntegrationError("TRACK_SELECTION_CHANGED", "Selected track changed before opening its instrument.")
        _pulse(outgoing, 115)
    return selected | {"editor_requested": True, "editor_verified": False}
