"""Explicit note ranges with native edits and opt-in isolated host compilation."""

import time
from pathlib import Path

from . import evec_focus, evec_work, expressions, note_selection, piapro_evec, singers, windows
from .errors import IntegrationError
from .storage import file_lock, state_dir, write_result


def prepare(data, selection, settings):
    info = expressions.inspect_native(data)
    ids = note_selection.resolve(info, selection)
    allowed = {'style', 'vibrato', 'evec_color', 'evec_release', 'evec_pronunciation', 'evec_consonant_repeat'}
    if not isinstance(settings, dict) or not settings or set(settings) - allowed:
        raise IntegrationError('EMPTY_EXPRESSION_CHANGE', 'Provide uniform expression settings for the explicit selection.')
    native = {k: v for k, v in settings.items() if k not in ('evec_color', 'evec_release')}
    for field, choices in [('evec_color', ('none', 'soft', 'power')), ('evec_release', ('none', 'short', 'long'))]:
        if field in settings and settings[field] not in choices:
            raise IntegrationError('INVALID_EVEC_CHOICE', 'Use supported Miku color/release choices.')
    # Validation runs before any live mutation, including the UI-only none case.
    expressions.validate_changes([{'id': ids[0], **settings, **{
        k: ('soft' if k == 'evec_color' else 'short') for k in ('evec_color', 'evec_release') if settings.get(k) == 'none'}}])
    staged = expressions.edit_native(data, [{'id': i, **native} for i in ids]) if native else data
    lookup = {n['id']: n for n in expressions.inspect_native(staged)['notes']}
    host_ids, native_changes = [], []
    for key in ids:
        changes = {}
        needs_host = False
        for field, short in [('evec_color', 'color'), ('evec_release', 'release')]:
            if field not in settings or settings[field] == lookup[key]['evec'][short] == 'none':
                continue
            if settings[field] == 'none' or lookup[key]['evec'][short] == 'none':
                needs_host = True
            else:
                changes[field] = settings[field]
        if changes and not needs_host:
            native_changes.append({'id': key, **changes})
        if needs_host:
            host_ids.append(key)
    if native_changes:
        staged = expressions.edit_native(staged, native_changes)
    if host_ids:
        evec_work.plan(staged, set(host_ids), settings)
    return staged, ids, host_ids, note_selection.summary(info, ids)


def set_file(path, output_path, expected_sha256, selection, settings):
    from .projects import read_expected
    source, before, _ = read_expected(path, expected_sha256)
    if source.suffix.lower() != '.ppsf' or Path(output_path).suffix.lower() != '.ppsf':
        raise IntegrationError('FORMAT_MISMATCH', 'Range expression writes require legacy PPSF input and output.')
    desired, ids, host_ids, selected = prepare(before, selection, settings)
    if host_ids:
        raise IntegrationError('EVEC_UI_REQUIRED', 'This file-only selection needs host-generated EVEC. Use the live range tool with explicit fallback.')
    return write_result(source, output_path, desired, expected_sha256) | {
        'live_state_updated': False, 'selection': selected,
        'notes': [n for n in expressions.inspect_native(desired)['notes'] if n['id'] in ids]}


def dismiss_own_file_error(root, path):
    if path is None:
        return False
    _, gui, _ = windows.api()
    dialogs = windows.dialogs(root['pid'])
    if len(dialogs) != 1 or dialogs[0]['title'] not in ('文件错误', 'File Error'):
        return False
    children = windows.children(dialogs[0]['handle'])
    text = ' '.join(c['title'] for c in children if c['class'] == 'Static')
    if Path(path).name not in text:
        return False
    ok = [c for c in children if c['visible'] and c['class'] == 'Button' and c['title'] in ('确定', 'OK')]
    if len(ok) != 1:
        return False
    gui.PostMessage(ok[0]['handle'], 0xF5, 0, 0)
    time.sleep(.3)
    return not windows.dialogs(root['pid'])


def set_live(selection, settings, expected_revision, allow_dialog_fallback=False):
    with file_lock(state_dir() / 'live-daw'):
        folder = singers._live_folder()
        snapshot, before = singers._capture(folder, 'range-before')
        if expressions.revision(before) != expected_revision:
            raise IntegrationError('PIAPRO_STATE_CHANGED', 'Read the current expression revision before applying note ranges.')
        desired, ids, host_ids, selected = prepare(before, selection, settings)
        if host_ids and not allow_dialog_fallback:
            raise IntegrationError('DIALOG_FALLBACK_REQUIRED', 'First color/release generation or disabling requires explicitly enabled isolated work-track UI.')
        if host_ids:
            evec_focus.require_desktop()
        context = {'frame': {}, 'attempted_path': None}
        root = windows.piapro_root()
        groups = 0
        try:
            if host_ids:
                desired, groups = evec_work.compile_native(desired, set(host_ids), settings, folder, context)
            if not host_ids and expressions.revision(desired) == expressions.revision(before):
                return {'verified': True, 'live_state_updated': False, 'disk_project_saved': False,
                        'revision': expected_revision, 'selection': selected, 'snapshot': snapshot,
                        'method': 'native selection already matches requested values', 'host_groups': 0}
            staged = write_result(None, str(folder / f'range-requested-{folder.name}.ppsf'), desired)
            context['attempted_path'] = staged['path']
            windows._send_file('import_ppsf', staged['path'], 10, allow_replace=True)
            readback, actual = singers._capture(folder, 'range-readback')
            verified = expressions.verify_native(desired, actual)
        except Exception as exc:
            restored = False
            try:
                frame = context['frame']
                if frame:
                    piapro_evec._click(root, (frame['left'] + 330 * frame['scale'], frame['top'] + 463 * frame['scale']))
                dismiss_own_file_error(root, context['attempted_path'])
                windows._send_file('import_ppsf', snapshot['path'], 10, allow_replace=True)
                _, recovered = singers._capture(folder, 'range-recovery')
                expressions.verify_native(before, recovered)
                restored = True
            except Exception:
                pass
            raise IntegrationError('EXPRESSION_RANGE_INCOMPLETE', f'{exc}; snapshot: {snapshot["path"]}; automatic restoration verified: {restored}. Inspect before retrying.') from exc
        return {'verified': True, 'live_state_updated': True, 'disk_project_saved': False,
                'method': 'isolated host-generated EVEC plus native merge/readback' if host_ids else 'native range expression import/readback',
                'host_groups': groups, 'selection': selected, 'snapshot': snapshot, 'readback': readback,
                'revision': expressions.revision(actual), 'notes': [n for n in verified['notes'] if n['id'] in ids]}
