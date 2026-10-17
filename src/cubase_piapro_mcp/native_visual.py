"""Local Win32 window capture and built-in Windows OCR for bounded UI fallbacks."""

from __future__ import annotations

import ctypes
import json
import os
import struct
import subprocess
from pathlib import Path

from . import windows
from .errors import IntegrationError


def capture(window, path, crop=None, scale=1):
    import win32ui
    from .cubase import _physical_coordinates

    _, gui, _ = windows.api()
    if not gui.IsWindow(window["handle"]) or not gui.IsWindowVisible(window["handle"]):
        raise IntegrationError("VISUAL_TARGET_CHANGED", "The verified Cubase window is no longer visible.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _physical_coordinates():
        left, top, right, bottom = gui.GetWindowRect(window["handle"])
        width, height = right - left, bottom - top
        x, y, cw, ch = crop or (0, 0, width, height)
        if (scale not in (1, 2) or min(width, height, cw, ch) <= 0
                or width * height > 24000000 or x < 0 or y < 0
                or x + cw > width or y + ch > height):
            raise IntegrationError("UNSUPPORTED_VISUAL_LAYOUT", "Window/capture bounds are not supported.")
        handle = gui.GetWindowDC(window["handle"])
        source = win32ui.CreateDCFromHandle(handle)
        full = source.CreateCompatibleDC()
        output = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        clipped = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(source, width, height)
            full.SelectObject(bitmap)
            if not ctypes.windll.user32.PrintWindow(window["handle"], full.GetSafeHdc(), 2):
                raise IntegrationError("WINDOW_CAPTURE_FAILED", "Cubase did not render the requested window.")
            clipped.CreateCompatibleBitmap(source, cw * scale, ch * scale)
            output.SelectObject(clipped)
            output.StretchBlt((0, 0), (cw * scale, ch * scale), full, (x, y), (cw, ch), 0xCC0020)
            clipped.SaveBitmapFile(output, str(path))
        finally:
            output.DeleteDC()
            full.DeleteDC()
            for item in (clipped, bitmap):
                if item.GetHandle():
                    gui.DeleteObject(item.GetHandle())
            source.DeleteDC()
            gui.ReleaseDC(window["handle"], handle)
    return {"path": str(path), "window_rect": [left, top, right, bottom],
            "crop": [x, y, cw, ch], "scale": scale}


def recognize(captured):
    script = Path(__file__).parent / "assets" / "windows_ocr.ps1"
    executable = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(script), "-ImagePath", captured["path"]],
                            capture_output=True, encoding="utf8", timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise IntegrationError("WINDOWS_OCR_UNAVAILABLE", "The built-in recognizer failed. No route selection was submitted. " + result.stderr[-600:])
    parsed = json.loads(result.stdout.lstrip("\ufeff"))
    rows = []
    for line in parsed["lines"]:
        words = line["words"]
        if not words:
            continue
        left, top = min(w["x"] for w in words), min(w["y"] for w in words)
        right = max(w["x"] + w["width"] for w in words)
        bottom = max(w["y"] + w["height"] for w in words)
        rows.append({"text": line["text"], "box": [left, top, right, bottom]})
    return captured | {"rows": rows, "engine": parsed["engine"], "language": parsed["language"]}


class Bitmap:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        if self.data[:2] != b"BM":
            raise IntegrationError("INVALID_UI_CAPTURE", "Expected a native bitmap.")
        self.offset = struct.unpack_from("<I", self.data, 10)[0]
        self.width, self.height = struct.unpack_from("<ii", self.data, 18)
        self.bits = struct.unpack_from("<H", self.data, 28)[0]
        compression = struct.unpack_from("<I", self.data, 30)[0]
        if self.bits not in (24, 32) or compression != 0 or self.width <= 0 or not self.height:
            raise IntegrationError("INVALID_UI_CAPTURE", "Unsupported native bitmap pixel layout.")
        self.stride = ((self.width * self.bits + 31) // 32) * 4

    def pixel(self, x, y):
        x, y = int(x), int(y)
        if not 0 <= x < self.width or not 0 <= y < abs(self.height):
            return (0, 0, 0)
        row = self.height - 1 - y if self.height > 0 else y
        offset = self.offset + row * self.stride + x * self.bits // 8
        b, g, r = self.data[offset:offset + 3]
        return r, g, b

    def selected_label(self, box):
        left, top, right, bottom = box
        middle = (top + bottom) / 2
        points = [(left - 3, middle), (right + 3, middle), (left, top - 2), (right, bottom + 2)]
        return sum(min(self.pixel(x, y)) >= 210 for x, y in points) >= 3

    def down_arrow(self, left, top, right, bottom, scale):
        """Locate one isolated descending triangle, excluding reset/round icons."""
        remaining = set()
        for y in range(max(0, int(top)), min(abs(self.height), int(bottom))):
            for x in range(max(0, int(left)), min(self.width, int(right))):
                rgb = self.pixel(x, y)
                if min(rgb) >= 145 and max(rgb) - min(rgb) <= 30:
                    remaining.add((x, y))
        matches = []
        while remaining:
            first = remaining.pop()
            component, pending = {first}, [first]
            while pending:
                x, y = pending.pop()
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
                    point = x + dx, y + dy
                    if point in remaining:
                        remaining.remove(point)
                        component.add(point)
                        pending.append(point)
            x1, x2 = min(p[0] for p in component), max(p[0] for p in component)
            y1, y2 = min(p[1] for p in component), max(p[1] for p in component)
            width, height = x2 - x1 + 1, y2 - y1 + 1
            if not (2.5 * scale <= width <= 12 * scale and 2 * scale <= height <= 8 * scale
                    and 1 <= width / height <= 3):
                continue
            rows = [sum(y == row for _, y in component) for row in range(y1, y2 + 1)]
            if any(after > before + 2 for before, after in zip(rows, rows[1:])):
                continue
            upper = sum(y <= (y1 + y2) / 2 for _, y in component)
            lower = len(component) - upper
            if upper > lower * 1.5:
                matches.append(((x1 + x2) / 2, (y1 + y2) / 2))
        if len(matches) != 1:
            raise IntegrationError("UNSUPPORTED_ROUTING_LAYOUT", "No unique dropdown arrow was verified. No content input was sent.")
        return matches[0]
