import os
import struct
from copy import deepcopy
from pathlib import Path

import pytest

from cubase_piapro_mcp import expressions, piapro_evec, ppsf, singers
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def fixtures():
    path = os.environ.get('CP_TEST_EXPRESSIONS')
    if not path:
        pytest.skip('Set CP_TEST_EXPRESSIONS to the local self-authored A/B fixtures')
    return Path(path)


def test_caption_pixel_count_preserves_zero_lost_by_local_chinese_ocr(monkeypatch):
    location = os.environ.get('CP_TEST_EVEC_ACCEPTANCE')
    if not location:
        pytest.skip('Set CP_TEST_EVEC_ACCEPTANCE to local actual caption captures')
    path = Path(location) / 'evec-count10-panel.bmp'
    assert piapro_evec._caption_binary_count(path, 1) == 10
    assert piapro_evec._caption_binary_count(path, 1.5) is None
    bitmap = piapro_evec.native_visual.Bitmap(path)
    original_pixel = bitmap.pixel
    bitmap.pixel = lambda x, y: (0, 0, 0) if (x, y) == (546, 14) else original_pixel(x, y)
    monkeypatch.setattr(piapro_evec.native_visual, 'Bitmap', lambda path: bitmap)
    assert piapro_evec._caption_binary_count(path, 1) is None


def test_evec_requires_fallback_before_any_host_access(monkeypatch):
    monkeypatch.setattr(singers, '_live_folder', lambda: pytest.fail('No host access without opt-in'))
    with pytest.raises(IntegrationError, match='DIALOG_FALLBACK_REQUIRED'):
        piapro_evec.set_part(0, 0, 'stale', color='soft')


def test_legacy_physical_note_edit_refuses_partial_evec_rewrites(fixtures):
    raw = (fixtures / 'evec-soft.ppsf').read_bytes()
    with pytest.raises(IntegrationError, match='EVEC_NOTE_REQUIRES_EXPRESSION_TOOL'):
        ppsf.edit(raw, [{'id': 'e0', 'note_num': 61}])


def test_singer_change_refuses_an_active_voice_specific_evec_chain(fixtures):
    raw = (fixtures / 'evec-soft.ppsf').read_bytes()
    project, chunks = ppsf.load(raw)
    entry = deepcopy(chunks['Project'].data.impl.vocaloid3_singers[0])
    entry.singer_name = 'A different captured singer preset'
    voice = ppsf.model.PpsfProjectV3SingerEntry.build(entry)
    with pytest.raises(IntegrationError, match='EVEC_SINGER_CHANGE_UNSUPPORTED'):
        singers.assign_native(raw, 2, voice)


def test_evec_global_comparison_resolves_new_derived_event_indexes(fixtures):
    before = (fixtures / 'baseline.ppsf').read_bytes()
    after = (fixtures / 'evec-soft.ppsf').read_bytes()
    assert piapro_evec._clip_signature(before) == piapro_evec._clip_signature(after)
    ids = {n['id'] for n in expressions.inspect_native(before)['notes']}
    assert len(piapro_evec._verify(before, after, ids, 'soft', None)) == 3


def test_evec_readback_rejects_unrequested_style_changes(fixtures):
    before = (fixtures / 'baseline.ppsf').read_bytes()
    changed = expressions.edit_native(before, [{'id': 't2:p0:n0', 'style': {'accent': 42}}])
    ids = {n['id'] for n in expressions.inspect_native(before)['notes']}
    with pytest.raises(IntegrationError, match='EVEC_READBACK_MISMATCH'):
        piapro_evec._verify(before, changed, ids, None, None)


@pytest.mark.parametrize('caption_visible', [True, False])
@pytest.mark.parametrize('release_label', ['R 引 巳 a 3 巳', 'VO R 巳 《 巳 3 巳'])
def test_palette_frame_does_not_mistake_a_vibrato_wave_for_its_top(tmp_path, monkeypatch, caption_visible, release_label):
    width, height = 600, 650
    data = bytearray(width * height * 3)
    for y in range(100, 124):
        for x in range(80, 476):
            i = ((height - 1 - y) * width + x) * 3
            data[i:i + 3] = b'\xc0\xc0\xc0'
    # A bright vibrato wave directly above the title center is not the frame.
    for y in range(82, 86):
        i = ((height - 1 - y) * width + 280) * 3
        data[i:i + 3] = b'\xff\xff\xff'
    bmp = tmp_path / 'frame.bmp'
    bmp.write_bytes(b'BM' + struct.pack('<IHHI', 54 + len(data), 0, 0, 54)
                   + struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0, len(data), 0, 0, 0, 0) + data)
    def capture(root, path, crop=None, scale=1):
        return {'path': str(bmp), 'crop': crop}
    def recognize(captured):
        if captured['crop'] is None:
            return captured | {'rows': [{'text': '符设 选中的符数：引', 'box': [210, 105, 350, 116]}] if caption_visible else [
                {'text': 'Cancel', 'box': [390, 559, 430, 567]}]}
        return captured | {'rows': [
            {'text': '音符设置 选中数量 3', 'box': [160, 10, 580, 32]},
            {'text': 'Col or', 'box': [270, 536, 336, 554]},
            {'text': release_label, 'box': [538, 536, 632, 554]}]}
    monkeypatch.setattr(piapro_evec.native_visual, 'capture', capture)
    monkeypatch.setattr(piapro_evec.native_visual, 'recognize', recognize)
    frame = piapro_evec._palette({}, tmp_path, 3, 0)
    assert frame['left'] == 80 and frame['top'] == 100
    assert frame['width'] == 396
    with pytest.raises(IntegrationError, match='EVEC_SELECTION_UNVERIFIED'):
        piapro_evec._palette({}, tmp_path, 2, 1)
