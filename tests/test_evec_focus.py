import pytest

from cubase_piapro_mcp import evec_focus
from cubase_piapro_mcp.errors import IntegrationError


class Bitmap:
    width, height = 1000, 800

    def __init__(self, keyboard=True, note=True):
        self.keyboard, self.note = keyboard, note

    def pixel(self, x, y):
        key = self.keyboard and 180 <= x < 240 and 200 <= y < 600 and (y - 200) % 24 < 12
        body = self.note and 270 <= x < 370 and 220 <= y < 235
        return (190, 190, 190) if key or body else (30, 40, 30)


def test_work_focus_detects_note_body_only_beyond_repeated_keyboard():
    x, y = evec_focus.note_body(Bitmap())
    assert 270 < x < 370 and 220 < y < 235


@pytest.mark.parametrize('keyboard,note', [(False, True), (True, False), (False, False)])
def test_work_focus_refuses_missing_keyboard_or_note(keyboard, note):
    with pytest.raises(IntegrationError, match='EVEC_WORK_VIEW_UNSUPPORTED'):
        evec_focus.note_body(Bitmap(keyboard, note))
