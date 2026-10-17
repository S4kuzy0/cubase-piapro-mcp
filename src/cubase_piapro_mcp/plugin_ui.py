"""Bounded Cubase 14 dark Channel-strip/plugin selector fallback."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import re
import time

from . import cubase, evec_focus, native_visual, windows
from .errors import IntegrationError


def label(text):
    return re.sub(r"\s+", "", text.casefold())


def native_edit_text(handle):
    """GetWindowText cannot read another process's Edit control contents."""
    _, gui, _ = windows.api()
    if gui.GetClassName(handle) != "Edit":
        raise IntegrationError("PLUGIN_FILTER_UNVERIFIED", "Expected the verified native search Edit.")
    text = ctypes.create_unicode_buffer(256)
    result = ctypes.c_size_t()
    send = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
        wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t))(("SendMessageTimeoutW", ctypes.windll.user32))
    if not send(handle, 0xD, len(text), ctypes.addressof(text), 2, 1500, ctypes.byref(result)) or result.value >= 255:
        raise IntegrationError("PLUGIN_FILTER_UNVERIFIED", "The search text read timed out or was truncated.")
    return text.value


def _click(window, point, rect):
    evec_focus.require_desktop()
    _, gui, _ = windows.api()
    windows.notify_activation(window["handle"])
    with cubase._physical_coordinates():
        if gui.GetWindowRect(window["handle"]) != tuple(rect):
            raise IntegrationError("PLUGIN_UI_CHANGED", "Window geometry changed before selector input.")
        left, top, right, bottom = rect
        px, py = point
        if not 0 < px < right - left or not 0 < py < bottom - top:
            raise IntegrationError("PLUGIN_UI_CHANGED", "Recognized target lies outside the verified window.")
        x, y = gui.ScreenToClient(window["handle"], (round(left + px), round(top + py)))
        for message, button in ((0x200, 0), (0x201, 1), (0x202, 0)):
            gui.PostMessage(window["handle"], message, button, y << 16 | x & 65535)


def _new_selector(root, before):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        matches = [w for w in windows.windows() if w["visible"] and w["pid"] == root["pid"]
                   and w["handle"] not in before and not w["title"] and w["class"].startswith("SteinbergWindowClass")]
        if len(matches) == 1:
            return matches[0], cubase._only_edit(matches[0]["handle"])
        time.sleep(.04)
    raise IntegrationError("PLUGIN_SELECTOR_UNAVAILABLE", "No unique native-search plugin selector appeared.")


def _cancel(popup, edit):
    _, gui, _ = windows.api()
    if gui.IsWindow(popup["handle"]) and gui.IsWindowVisible(popup["handle"]):
        gui.PostMessage(edit["handle"], 0x100, 0x1B, 1)
        gui.PostMessage(edit["handle"], 0x101, 0x1B, 0xC0000001)
        gui.PostMessage(popup["handle"], 0x100, 0x1B, 1)
        gui.PostMessage(popup["handle"], 0x101, 0x1B, 0xC0000001)


def filtered_choice(popup, edit, name, folder, refresh):
    evec_focus.require_desktop()
    _, gui, _ = windows.api()
    gui.SendMessage(edit["handle"], 0xC, 0, name)
    gui.SendMessage(edit["handle"], 0xB1, -1, -1)
    # WM_SETTEXT alone changes the visible Edit but does not notify Cubase's
    # filtering model. A real character notification was independently verified.
    gui.PostMessage(edit["handle"], 0x102, 0x20, 1)
    time.sleep(.15)
    if native_edit_text(edit["handle"]).strip() != name:
        raise IntegrationError("PLUGIN_FILTER_UNVERIFIED", "The actual search Edit differs from the requested plugin.")
    recognized = native_visual.recognize(native_visual.capture(popup, folder / "filtered.bmp", scale=2))
    with cubase._physical_coordinates():
        edit_bottom = gui.GetWindowRect(edit["handle"])[3] - recognized["window_rect"][1]
    matches = [row for row in recognized["rows"] if label(row["text"]) == label(name)
               and (row["box"][1] + row["box"][3]) / 4 > edit_bottom + 8]
    if len(matches) != 1:
        raise IntegrationError("PLUGIN_FILTER_UNVERIFIED", "No unique exact visible plugin leaf was recognized. Nothing was selected.")
    refresh()
    box = matches[0]["box"]
    _click(popup, ((box[0] + box[2]) / 4, (box[1] + box[3]) / 4), recognized["window_rect"])
    deadline = time.monotonic() + 3
    while gui.IsWindow(popup["handle"]) and gui.IsWindowVisible(popup["handle"]):
        if time.monotonic() > deadline:
            raise IntegrationError("PLUGIN_SELECTOR_PENDING", "The selector did not close; no success was assumed.")
        time.sleep(.04)
    return {"selector": "native Edit/WM_CHAR plus exact local OCR leaf and bounded window messages",
            "relative_pointer_messages": 1}


def add_track_plugin(dialog, name, folder, refresh):
    before = {w["handle"] for w in windows.windows() if w["visible"]}
    dialog.click_field(3)
    popup, edit = _new_selector(dialog.root, before)
    try:
        result = filtered_choice(popup, edit, name, folder, refresh)
        dialog.pointer_messages += result["relative_pointer_messages"]
        return result
    finally:
        _cancel(popup, edit)


def insert_anchor(recognized, bitmap, slot, scale, *, empty):
    rows = recognized["rows"]
    # These are static role labels, never fuzzy matches of a plugin/track name.
    # OCR appends adjacent icon glyphs to these header lines. Match the complete
    # first word; icon spelling is irrelevant, and all geometry/cell gates remain.
    role = lambda row: label(row["text"].strip().split()[0]).strip(".") if row["text"].strip() else ""
    headers = [r for r in rows if role(r) in ("inser", "lnser", "insert", "inserts")
               and r["box"][0] / 2 < 95 * scale]
    sends = [r for r in rows if role(r) == "sends" and r["box"][0] / 2 < 95 * scale]
    channels = [r for r in rows if role(r) == "channel" and r["box"][0] / 2 < 95 * scale]
    if len(headers) != 1 or len(sends) != 1 or len(channels) != 1 or not 0 <= slot < 4:
        raise IntegrationError("UNSUPPORTED_PLUGIN_LAYOUT", "Show the tested English dark Channel strip with Inserts and Sends.")
    box = headers[0]["box"]
    center = (box[1] + box[3]) / 4
    target_y = center + (26 + slot * 22) * scale
    target_x = box[0] / 2 + 50 * scale
    sends_y = (sends[0]["box"][1] + sends[0]["box"][3]) / 4
    if not (channels[0]["box"][3] / 2 < center < target_y < sends_y - 12 * scale):
        raise IntegrationError("UNSUPPORTED_PLUGIN_LAYOUT", "The requested insert row is not visibly inside the verified section.")
    if empty:
        samples = [bitmap.pixel((box[0] / 2 + dx * scale) * 2, target_y * 2) for dx in (8, 16, 24, 32, 40)]
        if any(max(rgb) > 85 or max(rgb) - min(rgb) > 25 for rgb in samples):
            raise IntegrationError("PLUGIN_SLOT_NOT_VISIBLY_EMPTY", "The row does not match the tested empty dark insert cell.")
    return target_x, target_y


def open_insert(root, slot, folder, refresh, *, empty=True):
    api, gui, _ = windows.api()
    evec_focus.require_desktop()
    if api.GetFileVersionInfo(root["executable"], "\\")["FileVersionMS"] >> 16 != 14:
        raise IntegrationError("UNSUPPORTED_PLUGIN_LAYOUT", "Only the tested Cubase 14 selector is enabled.")
    with cubase._physical_coordinates():
        rect = gui.GetWindowRect(root["handle"])
        scale = ctypes.windll.user32.GetDpiForWindow(root["handle"]) / 96
    if not 1 <= scale <= 2 or rect[2] - rect[0] < 700 or rect[3] - rect[1] < 650:
        raise IntegrationError("UNSUPPORTED_PLUGIN_LAYOUT", "Restore a sufficiently large project window.")
    captured = native_visual.capture(root, folder / "channel.bmp", crop=(0, 0, round(145 * scale), min(1100, rect[3] - rect[1])), scale=2)
    recognized = native_visual.recognize(captured)
    point = insert_anchor(recognized, native_visual.Bitmap(recognized["path"]), slot, scale, empty=empty)
    refresh()
    before = {w["handle"] for w in windows.windows() if w["visible"]}
    _click(root, point, rect)
    return _new_selector(root, before)


def choose_insert(root, slot, name, folder, refresh, *, empty=True):
    folder.mkdir(parents=True, exist_ok=False)
    popup, edit = open_insert(root, slot, folder, refresh, empty=empty)
    try:
        result = filtered_choice(popup, edit, name, folder, refresh)
        result["relative_pointer_messages"] += 1
        return result
    finally:
        _cancel(popup, edit)


def discard_new_plugin(root, track_name, plugin_name, folder):
    """Only for removing this transaction's plugin from a proven originally empty slot."""
    _, gui, _ = windows.api()
    dialogs = [w for w in windows.windows() if w["visible"] and w["pid"] == root["pid"]
               and w["title"] == "Cubase LE" and w["class"].startswith("SteinbergWindowClass")]
    if not dialogs:
        return False
    if len(dialogs) != 1:
        raise IntegrationError("PLUGIN_RECOVERY_DIALOG_UNVERIFIED", "More than one host question is open.")
    dialog = dialogs[0]
    buttons = [c for c in windows.children(dialog["handle"]) if c["class"] == "Button"]
    if sorted(c["title"] for c in buttons) != ["Discard", "Keep"]:
        raise IntegrationError("PLUGIN_RECOVERY_DIALOG_UNVERIFIED", "An unrelated host question is open.")
    recognized = native_visual.recognize(native_visual.capture(dialog, folder / "discard.bmp", scale=2))
    text = label(" ".join(r["text"] for r in recognized["rows"]))
    if "discardallchangesto" not in text or label(track_name + " - " + plugin_name) not in text:
        raise IntegrationError("PLUGIN_RECOVERY_DIALOG_UNVERIFIED", "The question does not name this transaction's track/plugin.")
    evec_focus.require_desktop()
    gui.SendMessage(next(c for c in buttons if c["title"] == "Discard")["handle"], 0xF5, 0, 0)
    return True
