"""Bounded drawing-surface focus for an already verified temporary work track."""

from collections import Counter

from . import native_visual, piapro_evec
from .errors import IntegrationError


def require_desktop():
    """Refuse UI work on a locked/screensaver desktop, before adding work tracks."""
    import ctypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    user.OpenInputDesktop.restype = ctypes.c_void_p
    handle = user.OpenInputDesktop(0, False, 1)
    name = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong()
    try:
        readable = handle and user.GetUserObjectInformationW(ctypes.c_void_p(handle), 2, name, ctypes.sizeof(name), ctypes.byref(size))
        if not readable or name.value.lower() != 'default':
            raise IntegrationError('INTERACTIVE_DESKTOP_REQUIRED', 'Unlock Windows and exit the screen saver before EVEC work-track UI. Native file/range edits remain available.')
    finally:
        if handle:
            user.CloseDesktop(ctypes.c_void_p(handle))


def note_body(bitmap):
    height = abs(bitmap.height)
    if bitmap.width < 900 or height < 700:
        raise IntegrationError('EVEC_WORK_VIEW_UNSUPPORTED', 'The verified work piano roll needs a sufficiently large window.')
    found = set()

    def light(x, y):
        red, green, blue = bitmap.pixel(x, y)
        return min(red, green, blue) >= 130 and max(red, green, blue) - min(red, green, blue) < 30

    for y in range(180, height - 100):
        x = 180
        while x < bitmap.width - 30:
            if not light(x, y):
                x += 4
                continue
            left = right = x
            while left > 160 and light(left - 1, y):
                left -= 1
            while right + 1 < bitmap.width and light(right + 1, y):
                right += 1
            x = right + 4
            if not 30 <= right - left <= 1000:
                continue
            top = bottom = y
            probe = right - 2
            while top > 160 and light(probe, top - 1):
                top -= 1
            while bottom + 1 < height - 80 and light(probe, bottom + 1):
                bottom += 1
            if 7 <= bottom - top <= 30:
                found.add((left, top, right + 1, bottom + 1))
    keys = Counter((left, right) for left, top, right, bottom in found
                   if 20 <= right - left <= 100 and right < bitmap.width / 3)
    keyboards = [pair for pair, count in keys.items() if count >= 12]
    if not keyboards:
        raise IntegrationError('EVEC_WORK_VIEW_UNSUPPORTED', 'No repeated native piano-key column was verified; no focus input was sent.')
    keyboard = max(keyboards, key=lambda pair: keys[pair])
    candidates = [r for r in found if r[0] >= keyboard[1] and r[2] - r[0] >= 40]
    if not candidates:
        raise IntegrationError('EVEC_WORK_VIEW_UNSUPPORTED', 'No temporary note body was verified to the right of the piano keys.')
    left, top, right, bottom = min(candidates)
    return (right - min(12, max(5, (right - left) // 8)), (top + bottom) // 2)


def focus_work_note(root, folder, sequence):
    # Caller must first prove the appended work track identity, active state,
    # words/pitches/durations and unique native piano-roll command. This helper
    # is never exposed as arbitrary pointer input or applied to a user part.
    captured = native_visual.capture(root, folder / f'work-{sequence}-focus.bmp')
    point = note_body(native_visual.Bitmap(captured['path']))
    piapro_evec._click(root, point)
    return {'method': 'verified temporary piano-note body; window-relative messages', 'point': point}
