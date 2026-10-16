"""Piapro native menus/dialogs with optional verified title-bar activation."""

from __future__ import annotations

import os
import ctypes
from ctypes import wintypes
import re
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from .errors import IntegrationError
from .storage import digest, file_lock, state_dir


def api():
    if os.name != "nt":
        raise IntegrationError(
            "WINDOWS_REQUIRED", "Live Piapro control requires Windows."
        )
    import win32api
    import win32gui
    import win32process

    return win32api, win32gui, win32process


def window_record(hwnd):
    wa, wg, wp = api()
    _, pid = wp.GetWindowThreadProcessId(hwnd)
    return {
        "handle": hwnd,
        "pid": pid,
        "title": wg.GetWindowText(hwnd),
        "class": wg.GetClassName(hwnd),
        "visible": bool(wg.IsWindowVisible(hwnd)),
    }


def windows():
    wa, wg, wp = api()
    records = []
    images = {}

    def visit(hwnd, _):
        record = window_record(hwnd)
        pid = record["pid"]
        if pid not in images:
            try:
                handle = wa.OpenProcess(0x410, False, pid)
                try:
                    images[pid] = wp.GetModuleFileNameEx(handle, 0)
                finally:
                    handle.Close()
            except Exception:
                images[pid] = ""
        if re.fullmatch(r"Cubase\d*\.exe", Path(images[pid]).name, re.I):
            record["executable"] = images[pid]
            records.append(record)
        return True

    wg.EnumWindows(visit, None)
    return records


def piapro_root():
    matches = [
        w
        for w in windows()
        if w["visible"]
        and w["class"] != "#32770"
        and "piapro studio" in w["title"].lower()
    ]
    if len(matches) != 1:
        raise IntegrationError(
            "PIAPRO_NOT_UNIQUE", "Open exactly one Piapro Studio editor in Cubase."
        )
    return matches[0]


def children(hwnd):
    _, wg, _ = api()
    found = []

    def visit(child, _):
        found.append(window_record(child))
        return True

    wg.EnumChildWindows(hwnd, visit, None)
    return found


def _menu_items(hwnd, menu, prefix=()):
    _, wg, _ = api()
    result = []
    # Piapro updates enable/disable flags when a popup is initialized.
    wg.SendMessageTimeout(hwnd, 0x117, menu, 0, 2, 1000)
    for index in range(wg.GetMenuItemCount(menu)):
        get_label = ctypes.windll.user32.GetMenuStringW
        get_label.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.UINT,
        ]
        get_label.restype = ctypes.c_int
        buffer = ctypes.create_unicode_buffer(1024)
        get_label(menu, index, buffer, len(buffer), 0x400)
        label = buffer.value.replace("&", "").split("\t")[0].strip()
        if not label:
            continue
        path = prefix + (label,)
        submenu = wg.GetSubMenu(menu, index)
        if submenu:
            result.extend(_menu_items(hwnd, submenu, path))
        else:
            command = wg.GetMenuItemID(menu, index)
            state = wg.GetMenuState(menu, index, 0x400)
            result.append(
                {
                    "path": " / ".join(path),
                    "command": command,
                    "window": hwnd,
                    "enabled": not bool(state & 3),
                    "checked": bool(state & 8),
                }
            )
    return result


def list_menus():
    _, wg, _ = api()
    root = piapro_root()
    candidates = [root] + children(root["handle"])
    result = []
    for record in candidates:
        menu = wg.GetMenu(record["handle"])
        if menu:
            result.extend(_menu_items(record["handle"], menu))
    return result


def choose_command(operation):
    specs = {
        "export_vsqx": (r"导出|export|書き出し", r"vocaloid.?4|vsqx|vocaloid.?3"),
        "export_ppsf": (r"导出|export|書き出し", r"piapro|ppsf|乐曲|楽曲"),
        "export_wav": (
            r"导出|export|書き出し|渲染|render",
            r"wav|wave|音频|オーディオ",
        ),
        "import_vsqx": (r"导入|import|読み込み", r"vocaloid|vsqx"),
        "import_ppsf": (r"导入|import|読み込み", r"piapro|ppsf|乐曲|楽曲"),
        "import_midi": (r"导入|import|読み込み", r"midi"),
    }
    if operation not in specs:
        raise IntegrationError("UNSUPPORTED_ACTION", operation)
    category, item = specs[operation]
    matches = [
        m
        for m in list_menus()
        if re.search(category, m["path"], re.I)
        and re.search(item, m["path"].split(" / ")[-1], re.I)
    ]
    if operation == "export_vsqx" and any(re.search(r"4", m["path"]) for m in matches):
        matches = [m for m in matches if re.search(r"4", m["path"])]
    if operation == "export_wav" and not any(m["enabled"] for m in matches):
        # VSTi disables the full mix export, but offers selected-track export.
        matches = [
            m
            for m in list_menus()
            if re.search(r"导出音轨|export track|トラック.*書き出し", m["path"], re.I)
        ]
    unique = {(m["window"], m["command"]): m for m in matches}
    if len(unique) != 1:
        raise IntegrationError(
            "MENU_NOT_UNIQUE",
            f"Cannot identify {operation}. Inspect piapro_list_menus for this version.",
        )
    menu = next(iter(unique.values()))
    if not menu["enabled"]:
        raise IntegrationError("COMMAND_DISABLED", menu["path"])
    return menu


def dialogs(pid):
    return [
        w
        for w in windows()
        if w["pid"] == pid and w["visible"] and w["class"] == "#32770"
    ]


def activate_editor(hwnd):
    wa, wg, _ = api()
    if wg.IsIconic(hwnd):
        wg.ShowWindow(hwnd, 9)
    try:
        wg.SetForegroundWindow(hwnd)
    except Exception:
        pass  # Windows may retain focus in the user's foreground application.
    if wg.GetForegroundWindow() == hwnd:
        return "native"
    if os.environ.get("CUBASE_PIAPRO_FOCUS_CLICK") != "1":
        raise IntegrationError(
            "FOCUS_REQUIRED",
            "Activate Piapro, or opt into the verified title-bar focus fallback with CUBASE_PIAPRO_FOCUS_CLICK=1.",
        )
    if piapro_root()["handle"] != hwnd or dialogs(window_record(hwnd)["pid"]):
        raise IntegrationError(
            "FOCUS_TARGET_CHANGED",
            "The editor or dialog state changed before activation.",
        )
    user32 = ctypes.windll.user32
    set_dpi = user32.SetThreadDpiAwarenessContext
    set_dpi.argtypes = [ctypes.c_void_p]
    set_dpi.restype = ctypes.c_void_p
    previous_dpi = set_dpi(ctypes.c_void_p(-4))
    cursor = wg.GetCursorPos()
    was_topmost = bool(wg.GetWindowLong(hwnd, -20) & 8)
    try:
        left, top, right, bottom = wg.GetWindowRect(hwnd)
        point = (left + 100, top + 20)
        if right - left < 300 or bottom - top < 100:
            raise IntegrationError(
                "FOCUS_REQUIRED", "The editor has no usable title bar."
            )
        wg.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)
        if wg.GetAncestor(wg.WindowFromPoint(point), 2) != hwnd:
            raise IntegrationError(
                "FOCUS_TARGET_CHANGED",
                "Another window covers the verified editor title bar.",
            )
        wa.SetCursorPos(point)
        wa.mouse_event(2, 0, 0, 0, 0)
        wa.mouse_event(4, 0, 0, 0, 0)
        time.sleep(0.08)
    finally:
        wa.SetCursorPos(cursor)
        if not was_topmost:
            wg.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x13)
        set_dpi(previous_dpi)
    if wg.GetForegroundWindow() != hwnd:
        raise IntegrationError(
            "FOCUS_REQUIRED",
            "Windows did not activate the editor. No menu command was sent.",
        )
    return "title_bar_click"


def _send_file(operation, path, timeout, allow_replace=False):
    _, wg, _ = api()
    root = piapro_root()
    from .live_midi import get_state

    host = get_state()
    if host.get("connected") and (
        host["state"]["playing"] or host["state"]["recording"]
    ):
        raise IntegrationError(
            "TRANSPORT_RUNNING", "Stop Cubase before a Piapro file operation."
        )
    existing = dialogs(root["pid"])
    if existing:
        raise IntegrationError(
            "DIALOG_BUSY",
            f"A Cubase dialog is already open: {[w['title'] for w in existing]}",
        )
    menu = choose_command(operation)
    # Piapro ignores menu commands while its editor is inactive.
    focus_method = activate_editor(root["handle"])
    wg.PostMessage(menu["window"], 0x111, menu["command"], 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = dialogs(root["pid"])
        for dialog in current:
            controls = children(dialog["handle"])
            if (
                operation == "import_ppsf"
                and allow_replace
                and dialog["title"] == "Piapro Studio"
            ):
                text = " ".join(c["title"] for c in controls if c["class"] == "Static")
                known = "当前乐曲数据将被覆盖" in text or bool(
                    re.search(r"current (song|song data).*overwrit", text, re.I | re.S)
                )
                yes = [
                    c
                    for c in controls
                    if c["class"] == "Button" and wg.GetDlgCtrlID(c["handle"]) == 6
                ]
                if known and len(yes) == 1:
                    wg.PostMessage(yes[0]["handle"], 0x00F5, 0, 0)
                    time.sleep(0.1)
                    continue
            if not re.search(
                r"导出|导入|打开|保存|export|import|open|save|render|書き出し|読み込み",
                dialog["title"],
                re.I,
            ):
                continue
            edits = [
                c
                for c in controls
                if c["class"] == "Edit" and wg.GetDlgCtrlID(c["handle"]) == 1148
            ]
            buttons = [
                c
                for c in controls
                if c["class"] == "Button" and wg.GetDlgCtrlID(c["handle"]) == 1
            ]
            if len(edits) == 1 and len(buttons) == 1:
                wg.SendMessage(edits[0]["handle"], 0x000C, 0, str(path))
                wg.PostMessage(buttons[0]["handle"], 0x00F5, 0, 0)
                quiet_since = None
                while time.monotonic() < deadline:
                    pending = dialogs(root["pid"])
                    if any(d["handle"] == dialog["handle"] for d in pending):
                        time.sleep(0.05)
                        continue
                    if pending:
                        details = [
                            d["title"]
                            + ": "
                            + " ".join(
                                c["title"]
                                for c in children(d["handle"])
                                if c["class"] == "Static"
                            )
                            for d in pending
                        ]
                        raise IntegrationError(
                            "PIAPRO_DIALOG", "Piapro reported: " + " | ".join(details)
                        )
                    quiet_since = quiet_since or time.monotonic()
                    if time.monotonic() - quiet_since >= 0.4:
                        scope = (
                            "selected_track"
                            if re.search(
                                r"导出音轨|export track|トラック", menu["path"], re.I
                            )
                            else "score"
                        )
                        return root | {
                            "scope": scope,
                            "menu_path": menu["path"],
                            "focus_method": focus_method,
                        }
                    time.sleep(0.05)
                raise IntegrationError(
                    "FILE_DIALOG_PENDING",
                    "The file dialog has not completed. Inspect the current dialog before retrying.",
                )
        time.sleep(0.1)
    raise IntegrationError(
        "FILE_DIALOG_UNSUPPORTED",
        f"No supported standard file dialog appeared for {operation}. No mouse fallback was used.",
    )


def _export_file(kind, path, timeout=30):
    expected = {"vsqx": ".vsqx", "ppsf": ".ppsf", "wav": ".wav"}
    path = Path(path).expanduser().resolve()
    if kind not in expected or path.suffix.lower() != expected[kind]:
        raise IntegrationError(
            "INVALID_EXPORT",
            "Choose vsqx, ppsf or wav and its matching output extension.",
        )
    if path.exists():
        raise IntegrationError("TARGET_EXISTS", "Choose a new export path.")
    path.parent.mkdir(parents=True, exist_ok=True)
    operation = _send_file("export_" + kind, path, min(timeout, 10))
    deadline = time.monotonic() + timeout
    previous = -1
    stable = 0
    while time.monotonic() < deadline:
        if path.exists():
            size = path.stat().st_size
            stable = stable + 1 if size == previous and size > 0 else 0
            previous = size
            if stable >= 5:
                data = path.read_bytes()
                if kind == "vsqx":
                    from .vsqx import inspect

                    inspect(data)
                elif kind == "ppsf":
                    from .ppsf import inspect

                    inspect(data)
                elif data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                    raise IntegrationError(
                        "INVALID_WAV", "The exported file is not a RIFF/WAVE file."
                    )
                return {
                    "path": str(path),
                    "sha256": digest(data),
                    "bytes": len(data),
                    "export_verified": True,
                    "scope": operation["scope"],
                    "menu_path": operation["menu_path"],
                    "focus_method": operation["focus_method"],
                }
        time.sleep(0.1)
    raise IntegrationError(
        "EXPORT_PENDING", f"Export has not produced a stable file: {path}"
    )


def export_file(kind, output_path, timeout=30):
    if not 1 <= timeout <= 45:
        raise IntegrationError(
            "INVALID_TIMEOUT", "Export timeout must be from 1 to 45 seconds."
        )
    with file_lock(state_dir() / "live-daw"):
        return _export_file(kind, output_path, timeout)


def import_file(path, expected_sha256, timeout=15, allow_empty=False):
    from .storage import read_file

    source, data, sha = read_file(path)
    if sha != expected_sha256.lower():
        raise IntegrationError(
            "FILE_CHANGED", "Inspect the current score before importing."
        )
    kind = {".vsqx": "vsqx", ".ppsf": "ppsf", ".mid": "midi", ".midi": "midi"}.get(
        source.suffix.lower()
    )
    if not kind:
        raise IntegrationError(
            "UNSUPPORTED_IMPORT", "Only VSQX, PPSF and MIDI are supported."
        )
    from . import vsqx, ppsf, midi

    {"vsqx": vsqx, "ppsf": ppsf, "midi": midi}[kind].inspect(data)
    with file_lock(state_dir() / "live-daw"):
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
        try:
            snapshot = _export_file(
                "ppsf", state_dir() / "snapshots" / (stamp + ".ppsf"), timeout
            )
        except IntegrationError as exc:
            if exc.code != "COMMAND_DISABLED" or not allow_empty:
                raise
            from .live_midi import get_state

            host = get_state()
            menus = list_menus()
            exports = [
                m
                for m in menus
                if re.search(r"导出|export|書き出し", m["path"], re.I)
                and re.search(r"piapro|vocaloid|midi", m["path"], re.I)
            ]
            undo = [
                m
                for m in menus
                if re.search(r"撤消|撤销|undo|元に戻す", m["path"], re.I)
            ]
            if (
                not exports
                or any(m["enabled"] for m in exports)
                or not undo
                or any(m["enabled"] for m in undo)
                or not host.get("connected")
                or host["state"]["playing"] != 0
                or host["state"]["recording"] != 0
            ):
                raise IntegrationError(
                    "EMPTY_STATE_UNVERIFIED",
                    "Empty initialization requires disabled score exports, no undo, and stopped MIDI host feedback.",
                ) from exc
            snapshot = {
                "empty_song": True,
                "verified_by": "disabled score exports, no undo, stopped host feedback",
            }
        staged = state_dir() / "imports" / (sha[:24] + source.suffix.lower())
        staged.parent.mkdir(parents=True, exist_ok=True)
        if not staged.exists():
            with staged.open("xb") as stream:
                stream.write(data)
        if digest(staged.read_bytes()) != sha:
            raise IntegrationError(
                "IMPORT_STAGE_CHANGED",
                "The staged import differs from the inspected source. No import was sent.",
            )
        try:
            imported = _send_file(
                "import_" + kind, staged, min(timeout, 10), allow_replace=kind == "ppsf"
            )
        except Exception as exc:
            raise IntegrationError(
                "IMPORT_INCOMPLETE",
                f"{exc}; original live snapshot: {snapshot.get('path', 'verified empty song')}",
            ) from exc
        return {
            "import_requested": True,
            "input": str(source),
            "sha256": sha,
            "snapshot": snapshot,
            "live_state_verified": False,
            "focus_method": imported["focus_method"],
            "next_step": "Export VSQX/PPSF and inspect the readback before claiming the intended live changes succeeded.",
        }
