"""MIDI routing through a bounded Cubase 14 English inspector fallback.

The MIDI Remote API has no routing property. Host UIDs authorize the track;
unfiltered selected menu labels and inspector text provide routing readback.
No native host object is accessed from a MIDI query callback.
"""

from __future__ import annotations

import ctypes
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import mido

from . import cubase, live_midi, native_visual, windows
from .errors import IntegrationError
from .storage import digest, file_lock, state_dir, write_result


def key(text):
    text = text.replace("AII MIDI", "All MIDI").replace("lnputs", "Inputs").replace("Studi0", "Studio")
    value = re.sub(r"[\W_]+", "", text.casefold())
    value = re.sub(r"a[il1]{1,2}m(?:idi|[i1l旧]*)inputs", "allmidiinputs", value)
    return value.replace("ch0rdpads", "chordpads").replace("allmidinputs", "allmidiinputs")


def port_alias(name):
    return re.sub(r" \d+$", "", name)


def canonical_route(text, direction, snapshot):
    standard = ["Not Connected"] + (["All MIDI Inputs", "Chord Pads"] if direction == "input" else [])
    aliases = [port_alias(name) for name in
               (mido.get_input_names() if direction == "input" else mido.get_output_names())]
    source, target = live_midi.selected_ports()
    bridge = {key(port_alias(source)), key(port_alias(target))}
    for label in standard + aliases:
        if key(text) == key(label):
            reason = "Dedicated MCP control port" if key(label) in bridge else None
            if label == "All MIDI Inputs":
                reason = "Bridge-port exclusion from All MIDI Inputs has not been independently verified"
            return {"label": label, "selectable": reason is None, "reason": reason,
                    "instrument_uid": None}
    match = re.match(r"^(\d+)\s*[.．]\s*(.*)", text)
    if direction == "output" and match:
        owners = [track for track in snapshot["tracks"] if track["kind"] == "instrument"
                  and track["plugin"] and key(match[2]).startswith(key(track["plugin"]))]
        if len(owners) == 1:
            owner = owners[0]
            return {"label": f"{match[1]}. {owner['plugin']} - MIDI In", "selectable": True,
                    "reason": None, "instrument_uid": owner["uid"]}
    return None


def menu_options(recognized, direction, snapshot):
    bitmap = native_visual.Bitmap(recognized["path"])
    options = []
    for row in sorted(recognized["rows"], key=lambda row: row["box"][1]):
        route = canonical_route(row["text"], direction, snapshot)
        if route is None:
            continue
        endpoint = "instrument:" + route["instrument_uid"] if route["instrument_uid"] else "port:" + route["label"]
        route["id"] = "route:" + digest((direction + "\n" + endpoint).encode())
        route["selected"] = bitmap.selected_label(row["box"])
        route["_box"] = row["box"]
        route["_scale"] = recognized["scale"]
        route["_rect"] = recognized["window_rect"]
        options.append(route)
    ids = [route["id"] for route in options]
    if not options or len(set(ids)) != len(ids):
        raise IntegrationError("ROUTING_MENU_UNSUPPORTED", "The menu has missing/ambiguous recognized routes. No selection was submitted.")
    return options


def _refresh(ctx):
    snapshot = live_midi.list_tracks()
    if snapshot["session"] != ctx["session"]:
        raise IntegrationError("ROUTING_CONTEXT_CHANGED", "The active project/mapping session changed.")
    track = live_midi.requested_track(snapshot, ctx["track_id"], snapshot["revision"])
    if snapshot["selected"]["uid"] != track["uid"]:
        raise IntegrationError("TRACK_SELECTION_CHANGED", "Selected host UID changed before routing input.")
    def identity(item):
        return (item["id"], item["name"], item["kind"],
                "" if ctx.get("output_change") and item["id"] == ctx["track_id"] else item["plugin"])
    if [identity(item) for item in snapshot["tracks"]] != [identity(item) for item in ctx["baseline"]["tracks"]]:
        raise IntegrationError("TRACK_LIST_CHANGED", "Unrelated host track identities changed during routing.")
    ctx.update(snapshot=snapshot, track=track)
    return snapshot


def _anchor(ctx, direction):
    api, gui, _ = windows.api()
    root = ctx["root"]
    cubase._idle(root)
    snapshot = _refresh(ctx)
    if direction == "output" and ctx["track"]["kind"] != "midi":
        raise IntegrationError("MIDI_OUTPUT_NOT_APPLICABLE", "Only MIDI tracks have a MIDI output selector. An instrument slot must never be used as a routing selector.")
    if api.GetFileVersionInfo(root["executable"], "\\")["FileVersionMS"] >> 16 != 14:
        raise IntegrationError("UNSUPPORTED_ROUTING_LAYOUT", "Only the validated Cubase 14 English inspector is supported.")
    with cubase._physical_coordinates():
        rect = gui.GetWindowRect(root["handle"])
        scale = ctypes.windll.user32.GetDpiForWindow(root["handle"]) / 96
    if not 1 <= scale <= 2 or rect[2] - rect[0] < 800 or rect[3] - rect[1] < 700:
        raise IntegrationError("UNSUPPORTED_ROUTING_LAYOUT", "Restore a sufficiently large Cubase project window first.")
    ctx["sequence"] += 1
    captured = native_visual.capture(root, ctx["folder"] / f"{ctx['sequence']}-inspector.bmp",
                                    crop=(0, 0, min(520, rect[2] - rect[0]), min(1000, rect[3] - rect[1])), scale=2)
    recognized = native_visual.recognize(captured)
    matches = [row for row in recognized["rows"] if re.search(r"\bRouting$", row["text"])
               and 220 < (row["box"][1] + row["box"][3]) / 4 / scale < 650]
    if len(matches) != 1:
        raise IntegrationError("UNSUPPORTED_ROUTING_LAYOUT", "Open the Routing inspector section. No unique verified anchor was recognized.")
    box = matches[0]["box"]
    anchor_y = (box[1] + box[3]) / 4
    point_y = anchor_y + (27 if direction == "input" else 77) * scale
    rows = [row for row in recognized["rows"] if abs((row["box"][1] + row["box"][3]) / 4 - point_y) < 8 * scale]
    channels = [row for row in recognized["rows"]
                if abs((row["box"][1] + row["box"][3]) / 4 - (anchor_y + 46 * scale)) < 8 * scale
                and ("nput" in row["text"] or "hannel" in row["text"])]
    if len(rows) != 1 or len(channels) != 1:
        raise IntegrationError("UNSUPPORTED_ROUTING_LAYOUT", "Routing fields/channel row do not match the validated inspector layout.")
    bitmap = native_visual.Bitmap(recognized["path"])
    arrow = bitmap.down_arrow(box[2] + 40 * scale, point_y * 2 - 16 * scale,
                              box[2] + 260 * scale, point_y * 2 + 16 * scale, scale * 2)
    return {"point": (arrow[0] / 2, arrow[1] / 2), "field_text": rows[0]["text"],
            "rect": rect, "scale": scale, "snapshot": snapshot}


def _popup(ctx, direction):
    _, gui, _ = windows.api()
    anchor = _anchor(ctx, direction)
    root = ctx["root"]
    before = {window["handle"] for window in windows.windows() if window["visible"]}
    windows.notify_activation(root["handle"])
    with cubase._physical_coordinates():
        if (gui.GetWindowRect(root["handle"]) != anchor["rect"]
                or ctypes.windll.user32.GetDpiForWindow(root["handle"]) / 96 != anchor["scale"]):
            raise IntegrationError("ROUTING_CONTEXT_CHANGED", "Window geometry/DPI changed before input.")
        left, top, right, bottom = anchor["rect"]
        px, py = anchor["point"]
        if not 70 * anchor["scale"] < px < 330 * anchor["scale"] or not 0 < py < bottom - top:
            raise IntegrationError("UNSUPPORTED_ROUTING_LAYOUT", "The inferred selector is outside the inspector bounds.")
        x, y = gui.ScreenToClient(root["handle"], (int(left + px), int(top + py)))
        position = y << 16 | x & 65535
        for message, button in ((0x200, 0), (0x201, 1), (0x202, 0)):
            gui.PostMessage(root["handle"], message, button, position)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        candidates = [window for window in windows.windows() if window["visible"]
                      and window["pid"] == root["pid"] and window["handle"] not in before
                      and not window["title"] and window["class"].startswith("SteinbergWindowClass")]
        if len(candidates) == 1:
            popup = candidates[0]
            edit = cubase._only_edit(popup["handle"])
            return popup, edit, anchor
        time.sleep(.025)
    raise IntegrationError("ROUTING_MENU_UNSUPPORTED", "No unique routing popup appeared. No selection was submitted.")


def _recognize_popup(ctx, popup, direction):
    ctx["sequence"] += 1
    result = native_visual.recognize(native_visual.capture(popup, ctx["folder"] / f"{ctx['sequence']}-{direction}.bmp", scale=2))
    return menu_options(result, direction, ctx["snapshot"])


def _close(popup, edit):
    _, gui, _ = windows.api()
    if not gui.IsWindow(popup["handle"]) or not gui.IsWindowVisible(popup["handle"]):
        return
    # Cubase handles cancellation on the popup, not its native search edit.
    for message, flags in ((0x100, 1), (0x101, 0xC0000001)):
        gui.PostMessage(popup["handle"], message, 0x1B, flags)
    deadline = time.monotonic() + 2
    while gui.IsWindow(popup["handle"]) and gui.IsWindowVisible(popup["handle"]):
        if time.monotonic() >= deadline:
            raise IntegrationError("ROUTING_POPUP_PENDING", "Routing popup did not cancel. Inspect before retrying.")
        time.sleep(.025)


def _read_direction(ctx, direction):
    popup, edit, anchor = _popup(ctx, direction)
    try:
        options = _recognize_popup(ctx, popup, direction)
        selected = [option for option in options if option["selected"]]
        if len(selected) != 1:
            raise IntegrationError("ROUTING_READBACK_UNVERIFIED", "No unique current route was identified in the unfiltered popup.")
        route = selected[0]
        field = key(anchor["field_text"])
        if route["instrument_uid"]:
            owner = next(track for track in ctx["snapshot"]["tracks"] if track["uid"] == route["instrument_uid"])
            if ctx["track"]["plugin"] != owner["plugin"] or key(owner["plugin"])[:10] not in field:
                raise IntegrationError("ROUTING_READBACK_UNVERIFIED", "Inspector and host plugin feedback do not confirm the selected instrument route.")
        elif key(route["label"]) not in field:
            raise IntegrationError("ROUTING_READBACK_UNVERIFIED", "Inspector text does not independently confirm the selected menu route.")
        public = lambda item: {name: value for name, value in item.items() if not name.startswith("_")}
        return {"selected": public(route), "options": [public(item) for item in options], "inspector_text": anchor["field_text"],
                "verified_by": "unfiltered selected menu label and inspector text; instrument output also checked against host plugin feedback"}
    finally:
        _close(popup, edit)


def _read(ctx):
    result = {"input": _read_direction(ctx, "input")}
    result["output"] = _read_direction(ctx, "output") if ctx["track"]["kind"] == "midi" else None
    snapshot = _refresh(ctx)
    identity = {"track_id": ctx["track_id"], "host_revision": snapshot["revision"],
                **{direction: None if value is None else {
                    "selected": value["selected"]["id"],
                    "options": [item["id"] for item in value["options"] if item["selectable"]]}
                   for direction, value in result.items()}}
    result.update(track=ctx["track"], host_revision=snapshot["revision"],
                  revision=digest(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()),
                  verified=True, native_routing_api=False, source="Cubase routing UI; Windows OCR",
                  recognized_routes_only=True,
                  checkpoint_directory=str(ctx["folder"]))
    return result


def _context(track_id, expected_revision, allow_dialog_fallback):
    if not allow_dialog_fallback:
        raise IntegrationError("DIALOG_FALLBACK_REQUIRED", "MIDI routing needs the validated inspector fallback. Explicitly set allow_dialog_fallback=true.")
    root = cubase.project_root()
    cubase._idle(root)
    if any(window["visible"] and window["pid"] == root["pid"] and not window["title"]
           and window["class"].startswith("SteinbergWindowClass")
           and any(control["class"] == "Edit" for control in windows.children(window["handle"]))
           for window in windows.windows()):
        raise IntegrationError("DIALOG_BUSY", "Close the existing Cubase selector before inspecting routing.")
    host = live_midi.get_state()
    if not host.get("connected") or host["state"]["playing"] or host["state"]["recording"]:
        raise IntegrationError("TRANSPORT_NOT_STOPPED", "Routing requires verified stopped transport.")
    snapshot = live_midi.list_tracks()
    track = live_midi.requested_track(snapshot, track_id, expected_revision)
    if track["kind"] not in ("midi", "instrument"):
        raise IntegrationError("MIDI_ROUTING_NOT_APPLICABLE", "Choose a MIDI track or an instrument MIDI input.")
    live_midi.select_track(track_id, expected_revision)
    folder = state_dir() / "routing-operations" / datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True, exist_ok=False)
    return {"root": root, "track_id": track_id, "snapshot": snapshot, "baseline": snapshot, "track": track,
            "session": snapshot["session"], "folder": folder, "sequence": 0}


def get_routing(track_id, expected_revision, allow_dialog_fallback=False):
    with file_lock(state_dir() / "live-daw"):
        return _read(_context(track_id, expected_revision, allow_dialog_fallback))


def _choose(ctx, direction, route_id, restoring=False):
    _, gui, _ = windows.api()
    popup, edit, _ = _popup(ctx, direction)
    try:
        options = _recognize_popup(ctx, popup, direction)
        choices = [option for option in options if option["id"] == route_id]
        if len(choices) != 1 or (not choices[0]["selectable"] and not choices[0]["selected"]
                                 and not (restoring and choices[0]["label"] == "All MIDI Inputs")):
            raise IntegrationError("ROUTING_OPTION_UNAVAILABLE", "The requested route is absent, ambiguous, or reserved for MCP control.")
        target = choices[0]
        if target["selected"]:
            return
        # This popup's native search edit does not reliably notify its custom
        # routing model through WM_SETTEXT. Select the unique recognized leaf
        # inside the verified popup; all coordinates remain window-relative.
        with cubase._physical_coordinates():
            rect = gui.GetWindowRect(popup["handle"])
            if list(rect) != target["_rect"]:
                raise IntegrationError("ROUTING_CONTEXT_CHANGED", "Popup geometry changed before selection.")
            left, top, right, bottom = target["_box"]
            x = (left + right) / 2 / target["_scale"]
            y = (top + bottom) / 2 / target["_scale"]
            if not 10 < x < rect[2] - rect[0] - 10 or not 60 < y < rect[3] - rect[1] - 10:
                raise IntegrationError("ROUTING_MENU_UNSUPPORTED", "Recognized route is outside safe popup content.")
            cx, cy = gui.ScreenToClient(popup["handle"], (int(rect[0] + x), int(rect[1] + y)))
            position = cy << 16 | cx & 65535
            for message, button in ((0x200, 0), (0x201, 1), (0x202, 0)):
                gui.PostMessage(popup["handle"], message, button, position)
        if direction == "output":
            ctx["output_change"] = True
        deadline = time.monotonic() + 2
        while gui.IsWindow(popup["handle"]) and gui.IsWindowVisible(popup["handle"]):
            if time.monotonic() >= deadline:
                raise IntegrationError("ROUTING_SELECTION_PENDING", "Route selection did not settle. Readback is required.")
            time.sleep(.025)
    finally:
        _close(popup, edit)


def set_routing(track_id, expected_revision, expected_routing_revision,
                input_route_id=None, output_route_id=None, allow_dialog_fallback=False):
    if input_route_id is None and output_route_id is None:
        raise IntegrationError("EMPTY_ROUTE_CHANGE", "Specify an input and/or MIDI output route ID from the latest routing readback.")
    with file_lock(state_dir() / "live-daw"):
        ctx = _context(track_id, expected_revision, allow_dialog_fallback)
        if output_route_id is not None and ctx["track"]["kind"] != "midi":
            raise IntegrationError("MIDI_OUTPUT_NOT_APPLICABLE", "An instrument slot cannot be replaced by a MIDI routing operation.")
        before = _read(ctx)
        if before["revision"] != expected_routing_revision:
            raise IntegrationError("ROUTING_CHANGED", "Read MIDI routing again before changing it.")
        requested = {direction: route_id for direction, route_id in (("input", input_route_id), ("output", output_route_id)) if route_id is not None}
        for direction, route_id in requested.items():
            options = [option for option in before[direction]["options"] if option["id"] == route_id]
            if len(options) != 1 or (not options[0]["selectable"] and not options[0]["selected"]):
                raise IntegrationError("ROUTING_OPTION_UNAVAILABLE", "Choose a selectable route from the current routing readback.")
        checkpoint = write_result(None, str(ctx["folder"] / "before.json"), json.dumps(before, ensure_ascii=False, indent=2).encode())
        try:
            for direction, route_id in requested.items():
                _choose(ctx, direction, route_id)
            after = _read(ctx)
            for direction in ("input", "output"):
                if before[direction] is None:
                    continue
                expected = requested.get(direction, before[direction]["selected"]["id"])
                if after[direction]["selected"]["id"] != expected:
                    raise IntegrationError("ROUTING_READBACK_UNVERIFIED", "Actual routing differs from the requested route.")
        except Exception as exc:
            restored = False
            try:
                for direction in requested:
                    _choose(ctx, direction, before[direction]["selected"]["id"], restoring=True)
                recovery = _read(ctx)
                restored = all(recovery[direction]["selected"]["id"] == before[direction]["selected"]["id"]
                               for direction in ("input", "output") if before[direction] is not None)
            except Exception:
                pass
            raise IntegrationError("ROUTING_CHANGE_INCOMPLETE", f"{exc}; checkpoint: {checkpoint['path']}; automatic route restoration verified: {restored}. Inspect before retrying.") from exc
        return after | {"checkpoint": checkpoint["path"], "disk_project_saved": False}
