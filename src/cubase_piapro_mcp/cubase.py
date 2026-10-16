"""Verified Cubase project/track operations, with a bounded native-dialog fallback."""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import live_midi, project_templates, windows
from .errors import IntegrationError
from .storage import digest, file_lock, read_file, state_dir, write_result


def project_windows():
    return [window for window in windows.windows() if window["visible"]
            and window["class"].startswith("SteinbergWindowClass")
            and re.search(r"\bProject\s*-", window["title"], re.I)]


def project_root():
    matches = project_windows()
    if len(matches) != 1:
        raise IntegrationError("CUBASE_PROJECT_NOT_UNIQUE", "Open exactly one Cubase project. This adapter is validated with Cubase 14 English project windows.")
    return matches[0]


def _idle(root):
    _, gui, _ = windows.api()
    pending = windows.dialogs(root["pid"])
    custom = [window for window in windows.windows() if window["visible"]
              and window["pid"] == root["pid"] and window["title"] == "Add Track"]
    if pending or custom or not gui.IsWindowEnabled(root["handle"]):
        raise IntegrationError("DIALOG_BUSY", "Handle the existing Cubase dialog before a project or track operation.")


def _checkpoint(root, snapshot, action):
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    path = state_dir() / "host-checkpoints" / (stamp + ".json")
    record = {"action": action, "window": root, "before": snapshot,
              "recovery": "Remove only newly added live UIDs, then restore the previous selected UID. Existing music is not rewritten by this additive operation."}
    write_result(None, str(path), json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))
    return str(path)


def _wait_dialog(pid, title, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [window for window in windows.windows() if window["visible"]
                   and window["pid"] == pid and window["title"] == title]
        if len(matches) == 1:
            return matches[0]
        time.sleep(.05)
    raise IntegrationError("HOST_DIALOG_UNSUPPORTED", f"No unique {title} dialog appeared. No further input was sent.")


def _only_edit(hwnd):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        edits = [control for control in windows.children(hwnd) if control["visible"] and control["class"] == "Edit"]
        if len(edits) == 1:
            return edits[0]
        if len(edits) > 1:
            break
        time.sleep(.025)
    raise IntegrationError("HOST_DIALOG_UNSUPPORTED", "Expected exactly one ready native edit control.")


def _button(hwnd, label):
    matches = [control for control in windows.children(hwnd)
               if control["class"] == "Button" and control["title"] == label]
    if len(matches) != 1:
        raise IntegrationError("HOST_DIALOG_UNSUPPORTED", f"Cannot identify the native {label} button.")
    return matches[0]


@contextmanager
def _physical_coordinates():
    setter = ctypes.windll.user32.SetThreadDpiAwarenessContext
    setter.argtypes = [ctypes.c_void_p]
    setter.restype = ctypes.c_void_p
    previous = setter(ctypes.c_void_p(-4))
    try:
        yield
    finally:
        setter(previous)


class AddTrackDialog:
    """The selector fallback is limited to the validated Cubase 14 English layout."""

    def __init__(self, root):
        self.root = root
        self.pointer_messages = 0
        self.focus_method = "native_control_messages"
        self.anchor = None

    def prepare_fallback(self):
        self.focus_method = windows.activate_window(self.root["handle"])
        api, gui, _ = windows.api()
        version = api.GetFileVersionInfo(self.root["executable"], "\\")["FileVersionMS"] >> 16
        edit = _only_edit(self.root["handle"])
        with _physical_coordinates():
            left, top, right, bottom = gui.GetWindowRect(self.root["handle"])
            el, et, er, eb = gui.GetWindowRect(edit["handle"])
            scale = ctypes.windll.user32.GetDpiForWindow(self.root["handle"]) / 96
            if (
                version != 14 or self.root["title"] != "Add Track" or not scale
                or not 438 <= (right - left) / scale <= 448
                or not 358 <= (bottom - top) / scale <= 368
                or not 230 <= (el - left) / scale <= 245
                or not 230 <= (et - top) / scale <= 250
            ):
                raise IntegrationError("UNSUPPORTED_ADD_TRACK_LAYOUT", "The dialog does not match the tested Cubase 14 English layout. No content-coordinate input was sent.")
            self.anchor = ((el + er) / 2 - left, (et + eb) / 2 - top, er - left, scale)

    def click_field(self, rows_above_name, *, count=False):
        _, gui, _ = windows.api()
        if self.anchor is None:
            raise IntegrationError("DIALOG_FALLBACK_REQUIRED", "Explicitly enable the validated dialog-selector fallback.")
        hwnd = self.root["handle"]
        if not gui.IsWindow(hwnd) or gui.GetWindowText(hwnd) != "Add Track":
            raise IntegrationError("HOST_DIALOG_CHANGED", "The Add Track dialog changed before selector input.")
        with _physical_coordinates():
            left, top, right, bottom = gui.GetWindowRect(hwnd)
            ax, ay, ar, scale = self.anchor
            current_scale = ctypes.windll.user32.GetDpiForWindow(hwnd) / 96
            if current_scale != scale or not 438 <= (right - left) / scale <= 448 or not 358 <= (bottom - top) / scale <= 368:
                raise IntegrationError("HOST_DIALOG_CHANGED", "Dialog DPI or layout changed before selector input.")
            point = (int(left + (ar - 40 * scale if count else ax)),
                     int(top + ay - rows_above_name * 30 * scale))
            if not left < point[0] < right or not top < point[1] < bottom:
                raise IntegrationError("HOST_DIALOG_CHANGED", "Selector anchor is outside the verified dialog.")
            x, y = gui.ScreenToClient(hwnd, point)
            position = y << 16 | x & 65535
            for message, button in ((0x200, 0), (0x201, 1), (0x202, 0)):
                gui.PostMessage(hwnd, message, button, position)
            self.pointer_messages += 1
        time.sleep(.18)

    def choose(self, rows_above_name, text):
        _, gui, _ = windows.api()
        existing = {window["handle"] for window in windows.windows() if window["visible"]}
        self.click_field(rows_above_name)
        deadline = time.monotonic() + 2
        popup = None
        while time.monotonic() < deadline:
            matches = [window for window in windows.windows()
                       if window["pid"] == self.root["pid"] and window["visible"]
                       and window["handle"] not in existing and not window["title"]
                       and window["class"].startswith("SteinbergWindowClass")]
            if len(matches) == 1:
                popup = matches[0]
                break
            time.sleep(.03)
        if popup is None:
            raise IntegrationError("HOST_SELECTOR_UNSUPPORTED", "No unique searchable selector appeared.")
        edit = _only_edit(popup["handle"])
        gui.SendMessage(edit["handle"], 0xC, 0, text)
        time.sleep(.15)
        for key in (0x28, 0x0D):
            gui.PostMessage(edit["handle"], 0x100, key, 1)
            gui.PostMessage(edit["handle"], 0x101, key, 0xC0000001)
            time.sleep(.12)
        deadline = time.monotonic() + 2
        while gui.IsWindow(popup["handle"]) and gui.IsWindowVisible(popup["handle"]):
            if time.monotonic() >= deadline:
                raise IntegrationError("HOST_SELECTOR_UNVERIFIED", "The selector did not close after the exact search. No track was submitted.")
            time.sleep(.025)

    def set_count_and_name(self, name, with_fallback):
        _, gui, _ = windows.api()
        if with_fallback:
            self.click_field(-1, count=True)
            gui.SendMessage(_only_edit(self.root["handle"])["handle"], 0xC, 0, "1")
            self.click_field(0)
        gui.SendMessage(_only_edit(self.root["handle"])["handle"], 0xC, 0, name)

    def submit(self):
        _, gui, _ = windows.api()
        button = _button(self.root["handle"], "Add Track")
        gui.PostMessage(button["handle"], 0xF5, 0, 0)


def _new_track(before, after, name, kind):
    old = {track["id"]: track for track in before["tracks"]}
    current = {track["id"]: track for track in after["tracks"]}
    if not old.keys() <= current.keys() or any(
        (old[key]["name"], old[key]["plugin"]) != (current[key]["name"], current[key]["plugin"])
        for key in old
    ):
        raise IntegrationError("UNVERIFIED_TRACK_ADDITION", "Existing track identities changed during addition. Inspect the checkpoint before retrying.")
    added = [track for track in after["tracks"] if track["id"] not in old]
    if len(added) != 1 or added[0]["name"] != name:
        raise IntegrationError("UNVERIFIED_TRACK_ADDITION", "Cubase did not return exactly one new track with the requested name.")
    track = added[0]
    if not track.get("uid"):
        raise IntegrationError("UNVERIFIED_TRACK_ADDITION", "The new track has not returned a native UID yet.")
    if kind == "piapro" and "piapro" not in track["plugin"].lower():
        raise IntegrationError("PIAPRO_NOT_LOADED", "The new track does not report a Piapro instrument.")
    if kind == "midi" and track.get("kind") != "midi":
        raise IntegrationError("UNVERIFIED_TRACK_ADDITION", "The new host channel is not a MIDI track.")
    if kind == "piapro" and track.get("kind") != "instrument":
        raise IntegrationError("PIAPRO_NOT_LOADED", "The Piapro plugin is not owned by a new instrument track.")
    if kind == "audio" and (track["plugin"] or track.get("kind") != "audio_or_sampler"):
        raise IntegrationError("UNVERIFIED_TRACK_ADDITION", "An unexpected instrument appeared on a non-instrument addition.")
    return track


def add_track(kind: str, name: str, expected_revision: str,
              allow_dialog_fallback: bool = False):
    if kind not in ("audio", "midi", "piapro"):
        raise IntegrationError("UNSUPPORTED_TRACK_TYPE", "Supported types are audio, midi, and piapro.")
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise IntegrationError("INVALID_TRACK_NAME", "Use a nonempty track name up to 80 characters, without control characters.")
    if kind in ("midi", "piapro") and not allow_dialog_fallback:
        raise IntegrationError("DIALOG_FALLBACK_REQUIRED", "This Cubase version needs bounded dialog-relative pointer messages for instrument/input selectors. Set allow_dialog_fallback=true, or create a preloaded project from a template.")
    with file_lock(state_dir() / "live-daw"):
        root = project_root()
        _idle(root)
        before = live_midi.list_tracks()
        if before["revision"] != expected_revision:
            raise IntegrationError("TRACK_LIST_CHANGED", "List live tracks again before adding a track.")
        if before["overflow"] or len(before["tracks"]) >= 64:
            raise IntegrationError("TRACK_CAPACITY_EXCEEDED", "This adapter verifies at most the first 64 supported mixer tracks.")
        if kind == "piapro" and any("piapro" in track["plugin"].lower() for track in before["tracks"]):
            raise IntegrationError("PIAPRO_ALREADY_LOADED", "Piapro permits only one instance per computer (E10). Use cubase_load_piapro to select/open the existing instance.")
        if any(not track.get("uid") or track.get("kind") == "unknown" for track in before["tracks"]):
            raise IntegrationError("TRACK_ID_PENDING", "Existing host identities are not ready. Read the live inventory again before adding a track.")
        checkpoint = _checkpoint(root, before, "add_" + kind)
        try:
            live_midi.command("add_" + kind)
            dialog = AddTrackDialog(_wait_dialog(root["pid"], "Add Track"))
            if kind in ("midi", "piapro"):
                dialog.prepare_fallback()
                if kind == "piapro":
                    dialog.choose(3, "Piapro Studio VSTi")
                dialog.choose(2 if kind == "piapro" else 3, "Not Connected")
            dialog.set_count_and_name(name, kind in ("midi", "piapro"))
            dialog.submit()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                pending = windows.dialogs(root["pid"])
                if pending:
                    details = " | ".join(window["title"] + ": " + " ".join(
                        control["title"] for control in windows.children(window["handle"])
                        if control["class"] == "Static") for window in pending)
                    code = "PIAPRO_SINGLE_INSTANCE" if "(E10)" in details else "HOST_DIALOG"
                    raise IntegrationError(code, details)
                if any(window["visible"] and window["title"] == "Add Track" for window in windows.windows()):
                    time.sleep(.1)
                    continue
                try:
                    after = live_midi.list_tracks()
                except IntegrationError as exc:
                    if exc.code != "TRACK_DRIVER_REQUIRED":
                        raise
                    time.sleep(.1)
                    continue
                try:
                    added = _new_track(before, after, name, kind)
                except IntegrationError as exc:
                    if exc.code not in ("UNVERIFIED_TRACK_ADDITION", "PIAPRO_NOT_LOADED"):
                        raise
                    time.sleep(.1)
                    continue
                return {"verified": True, "track": added, "revision": after["revision"],
                        "checkpoint": checkpoint, "disk_project_saved": False,
                        "method": "MIDI Remote AddTrack command; native edit/button messages",
                        "dialog_relative_pointer_messages": dialog.pointer_messages,
                        "focus_method": dialog.focus_method,
                        "midi_input": {"requested": "Not Connected" if kind != "audio" else "Cubase defaults",
                                       "host_routing_readback": False}}
            raise IntegrationError("TRACK_ADDITION_PENDING", "The native operation did not settle before timeout.")
        except Exception as exc:
            raise IntegrationError("TRACK_OPERATION_INCOMPLETE", f"{exc}; additive-operation checkpoint: {checkpoint}. Inspect live state before retrying.") from exc


def load_piapro(expected_revision: str, track_name="Piapro", allow_dialog_fallback=False):
    snapshot = live_midi.list_tracks()
    if snapshot["revision"] != expected_revision:
        raise IntegrationError("TRACK_LIST_CHANGED", "List live tracks again before loading Piapro.")
    candidates = [track for track in snapshot["tracks"]
                  if track.get("kind") == "instrument" and "piapro" in track["plugin"].lower()]
    if not candidates and any("piapro" in track["plugin"].lower() for track in snapshot["tracks"]):
        raise IntegrationError("PIAPRO_OWNER_UNRESOLVED", "A channel references Piapro but no instrument-owner UID was identified. Inspect the routing before adding an instance.")
    if len(candidates) > 1:
        raise IntegrationError("PIAPRO_MULTIPLE_INSTANCES", "Multiple Piapro tracks exist. Resolve E10 before using this adapter.")
    added = None
    if not candidates:
        added = add_track("piapro", track_name, expected_revision, allow_dialog_fallback)
        snapshot = live_midi.list_tracks()
        candidates = [track for track in snapshot["tracks"] if track["id"] == added["track"]["id"]]
    track = candidates[0]
    with file_lock(state_dir() / "live-daw"):
        root = project_root()
        _idle(root)
        selection = live_midi.select_track(track["id"], snapshot["revision"])
        editors = [window for window in windows.windows() if window["pid"] == root["pid"]
                   and window["visible"] and window["class"].startswith("Afx:")
                   and "piapro studio" in window["title"].lower()]
        if not editors:
            live_midi.open_instrument_editor(track["id"], snapshot["revision"])
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                editor = windows.piapro_root()
                menus = windows.list_menus()
                if editor["pid"] == root["pid"] and any(re.search(r"导出|export|書き出し", menu["path"], re.I) for menu in menus):
                    return {"verified": True, "reused_existing_instance": added is None,
                            "track": track, "selection": selection, "editor": editor,
                            "editor_verified_by": "unique native Piapro editor and actual export menus",
                            "addition": added}
            except IntegrationError as exc:
                if exc.code != "PIAPRO_NOT_UNIQUE":
                    raise
            time.sleep(.1)
        raise IntegrationError("PIAPRO_EDITOR_UNVERIFIED", "Piapro host identity was found, but no unique usable native editor was verified.")


def _executable():
    configured = os.environ.get("CUBASE_PIAPRO_CUBASE_EXE")
    candidates = [Path(configured)] if configured else list(
        (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Steinberg").glob("Cubase */Cubase*.exe"))
    matches = [path.resolve() for path in candidates if path.is_file() and re.fullmatch(r"Cubase\d*\.exe", path.name, re.I)]
    if len(matches) != 1:
        raise IntegrationError("CUBASE_EXECUTABLE_REQUIRED", "Set CUBASE_PIAPRO_CUBASE_EXE to the intended installed Cubase executable.")
    return matches[0]


def create_project(template_id: str, project_directory: str, name: str | None = None,
                   open_project=True, timeout=40):
    if not 1 <= timeout <= 45:
        raise IntegrationError("INVALID_TIMEOUT", "Project startup timeout must be between 1 and 45 seconds.")
    with file_lock(state_dir() / "live-daw"):
        if open_project and project_windows():
            raise IntegrationError("PROJECT_ALREADY_OPEN", "Save/close the current project first. This template workflow never closes an existing project or acknowledges discard prompts.")
        if open_project and any(window["visible"] and window["title"] == "Checking Licenses..." for window in windows.windows()):
            raise IntegrationError("CUBASE_STARTING", "Cubase is still checking licenses. Wait for startup or resolve the displayed licensing state first.")
        executable = _executable() if open_project else None
        created = project_templates.create_copy(template_id, project_directory, name)
        if not open_project:
            return created
        subprocess.Popen([str(executable), created["path"]], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        expected_name = Path(created["path"]).stem
        while time.monotonic() < deadline:
            matches = project_windows()
            if len(matches) == 1 and re.search(r"Project\s*-\s*" + re.escape(expected_name) + r"(?:\s*\*)?$", matches[0]["title"]):
                try:
                    snapshot = live_midi.list_tracks()
                except IntegrationError:
                    time.sleep(.15)
                    continue
                expected = created["expected_tracks"]
                actual = [{"name": track["name"], "plugin": track["plugin"]} for track in snapshot["tracks"]]
                if expected and actual != expected:
                    raise IntegrationError("TEMPLATE_LIVE_MISMATCH", f"The template opened but its live track inventory differs. Created project retained at {created['path']}.")
                created.update(live_state_verified=True, window=matches[0], tracks=snapshot)
                if any("piapro" in track["plugin"].lower() for track in snapshot["tracks"]):
                    # load_piapro has its own DAW lock, so opening is a separate explicit tool call.
                    created["next_step"] = "Call cubase_load_piapro with the returned track revision to open/verify its editor."
                return created
            time.sleep(.15)
        return created | {"launch_requested": True, "live_state_verified": False,
                          "next_step": "The project file exists, but startup was not verified. Inspect daw_get_status and licensing/dialog state; do not repeat creation into this directory."}
