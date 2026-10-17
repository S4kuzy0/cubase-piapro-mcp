import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cubase_piapro_mcp import expression_ranges as ranges, expressions, storage
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def native():
    root = os.environ.get('CP_TEST_EXPRESSIONS')
    if not root:
        pytest.skip('Set CP_TEST_EXPRESSIONS to local self-authored native references')
    return (Path(root) / 'baseline.ppsf').read_bytes()


def test_range_native_settings_preserve_every_unselected_note(native):
    selection = {'ranges': [{'track_index': 2, 'part_index': 0, 'start_note_index': 1}]}
    result, ids, host, selected = ranges.prepare(native, selection,
        {'style': {'accent': 81}, 'evec_pronunciation': 'strong', 'evec_consonant_repeat': 3})
    old, new = expressions.inspect_native(native)['notes'], expressions.inspect_native(result)['notes']
    assert ids == ['t2:p0:n1', 't2:p0:n2'] and host == [] and selected['note_count'] == 2
    assert new[0] == old[0]
    assert new[1]['phoneme'] == 'a' and new[2]['phoneme'] == '4 4 4 4#6 a'
    assert all(n['style']['accent'] == 81 for n in new[1:])


def test_first_generation_is_planned_without_rewriting_original(native):
    result, ids, host, _ = ranges.prepare(native, {'note_ids': ['t2:p0:n1']}, {'evec_color': 'soft'})
    assert result == native and ids == host == ['t2:p0:n1']


def test_already_disabled_range_is_a_native_noop(native):
    result, _, host, _ = ranges.prepare(native, {'note_ids': ['t2:p0:n0']}, {'evec_color': 'none', 'evec_release': 'none'})
    assert result == native and not host


@pytest.mark.parametrize('settings', [{}, {'id': 't2:p0:n2'}, {'style': {'accent': 101}},
                                       {'evec_color': 'unknown'}, {'evec_consonant_repeat': True}])
def test_range_settings_reject_invalid_values_before_writes(native, settings):
    with pytest.raises(IntegrationError):
        ranges.prepare(native, {'note_ids': ['t2:p0:n0']}, settings)


def test_file_first_generation_and_stale_hash_leave_no_output(native, tmp_path):
    source = tmp_path / 'source.ppsf'; source.write_bytes(native)
    output = tmp_path / 'new.ppsf'
    for sha, code in [('0' * 64, 'FILE_CHANGED'), (storage.digest(native), 'EVEC_UI_REQUIRED')]:
        with pytest.raises(IntegrationError, match=code):
            ranges.set_file(str(source), str(output), sha, {'note_ids': ['t2:p0:n1']}, {'evec_color': 'soft'})
        assert not output.exists() and source.read_bytes() == native


def _live(monkeypatch, tmp_path, native):
    monkeypatch.setenv('CUBASE_PIAPRO_STATE_DIR', str(tmp_path / 'state'))
    folder = tmp_path / 'operation'; folder.mkdir()
    snapshot = folder / 'before.ppsf'; snapshot.write_bytes(native)
    monkeypatch.setattr(ranges.singers, '_live_folder', lambda: folder)
    monkeypatch.setattr(ranges.windows, 'piapro_root', lambda: {'handle': 1, 'pid': 2})
    return folder, snapshot


def test_stale_live_revision_refuses_any_import(native, tmp_path, monkeypatch):
    _, snapshot = _live(monkeypatch, tmp_path, native)
    monkeypatch.setattr(ranges.singers, '_capture', lambda *a: ({'path': str(snapshot)}, native))
    monkeypatch.setattr(ranges.windows, '_send_file', lambda *a, **k: pytest.fail('No stale import'))
    with pytest.raises(IntegrationError, match='PIAPRO_STATE_CHANGED'):
        ranges.set_live({'note_ids': ['t2:p0:n0']}, {'style': {'accent': 81}}, 'stale')


def test_missing_fallback_and_locked_desktop_refuse_before_work_import(native, tmp_path, monkeypatch):
    _, snapshot = _live(monkeypatch, tmp_path, native)
    monkeypatch.setattr(ranges.singers, '_capture', lambda *a: ({'path': str(snapshot)}, native))
    monkeypatch.setattr(ranges.windows, '_send_file', lambda *a, **k: pytest.fail('No work import'))
    with pytest.raises(IntegrationError, match='DIALOG_FALLBACK_REQUIRED'):
        ranges.set_live({'note_ids': ['t2:p0:n0']}, {'evec_color': 'soft'}, expressions.revision(native))
    def locked():
        raise IntegrationError('INTERACTIVE_DESKTOP_REQUIRED', 'locked')
    monkeypatch.setattr(ranges.evec_focus, 'require_desktop', locked)
    with pytest.raises(IntegrationError, match='INTERACTIVE_DESKTOP_REQUIRED'):
        ranges.set_live({'note_ids': ['t2:p0:n0']}, {'evec_color': 'soft'}, expressions.revision(native), True)


@pytest.mark.parametrize('recovery_good', [True, False])
def test_failed_full_readback_restores_snapshot_and_reports_actual_recovery(native, tmp_path, monkeypatch, recovery_good):
    _, snapshot = _live(monkeypatch, tmp_path, native)
    bad = expressions.edit_native(native, [{'id': 't2:p0:n2', 'style': {'accent': 99}}])
    reads = iter([native, bad, native if recovery_good else bad])
    monkeypatch.setattr(ranges.singers, '_capture', lambda *a: ({'path': str(snapshot)}, next(reads)))
    sent = []
    monkeypatch.setattr(ranges.windows, '_send_file', lambda *a, **k: sent.append(a[1]))
    monkeypatch.setattr(ranges, 'dismiss_own_file_error', lambda *a: False)
    with pytest.raises(IntegrationError, match=f'automatic restoration verified: {recovery_good}'):
        ranges.set_live({'note_ids': ['t2:p0:n0']}, {'style': {'accent': 81}}, expressions.revision(native))
    assert len(sent) == 2 and sent[-1] == str(snapshot)


@pytest.mark.parametrize('own', [True, False])
def test_recovery_only_dismisses_error_naming_its_own_staged_file(monkeypatch, own):
    sent = []
    gui = SimpleNamespace(PostMessage=lambda *a: sent.append(a))
    monkeypatch.setattr(ranges.windows, 'api', lambda: (None, gui, None))
    dialog = [{'title': '文件错误', 'handle': 10}]
    monkeypatch.setattr(ranges.windows, 'dialogs', lambda pid: [] if sent else dialog)
    monkeypatch.setattr(ranges.windows, 'children', lambda h: [
        {'class': 'Static', 'visible': True, 'title': 'range-requested.ppsf' if own else 'other-song.ppsf'},
        {'class': 'Button', 'title': '确定', 'visible': True, 'handle': 11}])
    monkeypatch.setattr(ranges.time, 'sleep', lambda n: None)
    assert ranges.dismiss_own_file_error({'pid': 2}, Path('range-requested.ppsf')) is own
    assert bool(sent) is own
