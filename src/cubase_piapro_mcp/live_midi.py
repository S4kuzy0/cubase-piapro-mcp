"""Small request/feedback bridge through Cubase MIDI Remote."""

from __future__ import annotations

import random
import time
import mido

from .errors import IntegrationError
from .storage import file_lock, state_dir

INPUT_NAME = "CP MCP From Cubase"
OUTPUT_NAME = "CP MCP To Cubase"
FIELDS = ("playing", "recording", "volume", "pan", "mute", "solo")


def ports():
    return {"inputs": mido.get_input_names(), "outputs": mido.get_output_names()}


def selected_ports():
    available = ports()
    inputs = [p for p in available["inputs"] if INPUT_NAME in p]
    outputs = [p for p in available["outputs"] if OUTPUT_NAME in p]
    if len(inputs) != 1 or len(outputs) != 1:
        raise IntegrationError(
            "MIDI_SETUP_REQUIRED",
            f"Create the two named loopback MIDI ports and load the Cubase driver: {INPUT_NAME}; {OUTPUT_NAME}.",
        )
    return inputs[0], outputs[0]


def _query(incoming, outgoing, timeout=1.5):
    nonce = random.randint(1, 126)
    for _ in incoming.iter_pending():
        pass
    outgoing.send(mido.Message("control_change", channel=15, control=119, value=0))
    time.sleep(0.02)
    outgoing.send(mido.Message("control_change", channel=15, control=119, value=nonce))
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
        outgoing.send(
            mido.Message("control_change", channel=15, control=cc, value=wire)
        )
        if action in ("play", "stop", "record"):
            time.sleep(0.05)
            outgoing.send(
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
