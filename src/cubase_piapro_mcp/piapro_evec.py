"""Bounded EVEC host-generation fallback for the currently open piano-roll part."""

from __future__ import annotations

import re
import time

from . import cubase, expressions, native_visual, ppsf, singers, windows
from .errors import IntegrationError
from .storage import file_lock, state_dir, write_result


def _menu(root, suffix):
    _, gui, _ = windows.api()
    matches = [m for m in windows.list_menus() if m['path'].endswith(suffix)]
    if len(matches) != 1 or not matches[0]['enabled']:
        raise IntegrationError('PIAPRO_PART_VIEW_REQUIRED', 'Open the requested part in the Piapro piano roll first; no EVEC choice was submitted.')
    gui.PostMessage(root['handle'], 0x111, matches[0]['command'], 0)


def _child(root):
    candidates = [w for w in windows.children(root['handle']) if w['visible'] and w['class'] == '#32770']
    if len(candidates) != 1:
        raise IntegrationError('EVEC_UI_UNSUPPORTED', 'No unique native Piapro drawing surface.')
    return candidates[0]


def _click(root, point):
    _, gui, _ = windows.api()
    child = _child(root)
    with cubase._physical_coordinates():
        left, top, right, bottom = gui.GetWindowRect(root['handle'])
        x, y = point
        if not 0 < x < right - left or not 0 < y < bottom - top:
            raise IntegrationError('EVEC_UI_UNSUPPORTED', 'The verified EVEC target moved outside its window.')
        cx, cy = gui.ScreenToClient(child['handle'], (int(left + x), int(top + y)))
        position = cy << 16 | cx & 65535
        for message, button in ((0x200, 0), (0x201, 1), (0x202, 0)):
            gui.PostMessage(child['handle'], message, button, position)
    time.sleep(.25)


def _caption_from_cancel(bitmap, rows):
    """Locate a candidate title bar when full-window OCR merges it with grid lines.

    This only narrows a crop. The enlarged real caption and both EVEC headings
    must still pass before any parameter input is allowed.
    """
    labels = [r for r in rows if r['text'].strip().lower() == 'cancel']
    candidates = set()
    bright = lambda x, y: min(bitmap.pixel(x, y)) >= 120 and max(bitmap.pixel(x, y)) - min(bitmap.pixel(x, y)) < 25
    for label in labels:
        cx = int((label['box'][0] + label['box'][2]) / 2)
        cy = int((label['box'][1] + label['box'][3]) / 2)
        for y in range(max(0, cy - 960), max(0, cy - 350)):
            if not bright(cx, y):
                continue
            left = right = cx
            while left > 0 and bright(left - 1, y):
                left -= 1
            while right + 1 < bitmap.width and bright(right + 1, y):
                right += 1
            scale = (right - left + 1) / 396
            if not .8 <= scale <= 2 or abs(left + 330 * scale - cx) > 14 * scale or abs(y + 463 * scale - cy) > 22 * scale:
                continue
            tops = [py for py in range(max(0, y - round(22 * scale)), y + 1)
                    if bright((left + right) // 2, py) and bright(left + 5, py) and bright(right - 5, py)]
            if tops and abs(min(tops) + 463 * scale - cy) <= 9 * scale:
                candidates.add((left, min(tops), right - left + 1))
    if len(candidates) != 1:
        raise IntegrationError('EVEC_SELECTION_UNVERIFIED', 'No unique note-settings caption/frame was recognized.')
    left, top, width = candidates.pop()
    return {'box': [left + 80, top + 5, left + width - 80, top + 18]}


def _caption_binary_count(path, scale):
    """Read exact observed 0/1 glyphs when local Chinese OCR loses the zero.

    Restricted to the captured default-size Piapro caption font. Unknown
    glyphs/layouts return None; the ordinary exact OCR check then still applies.
    No expected count is used to infer a missing digit.
    """
    if abs(scale - 1) > .001:
        return None
    bitmap = native_visual.Bitmap(path)
    if bitmap.width != 792 or abs(bitmap.height) < 36:
        return None
    templates = {
        ('011', '101', '001', '001', '001', '001', '001', '001'): '1',
        ('01111', '01001', '10001', '10001', '10001', '01001', '01001', '00110'): '0',
        ('1', '0', '0', '0', '0', '1'): ':',
        ('010', '010', '001', '001', '001', '001', '001', '001', '010', '100'): ')',
    }
    ink = lambda x, y: max(bitmap.pixel(x, y)) < 128
    glyphs = []
    x = 40
    while x < bitmap.width - 80:
        if not any(ink(x, y) for y in range(8, 35)):
            x += 1
            continue
        left = x
        while x < bitmap.width - 80 and any(ink(x, y) for y in range(8, 35)):
            x += 1
        ys = [y for y in range(8, 35) if any(ink(px, y) for px in range(left, x))]
        top, bottom = min(ys), max(ys) + 1
        rows = tuple(''.join('1' if ink(px, py) else '0' for px in range(left, x, 2))
                     for py in range(top, bottom, 2))
        glyphs.append(templates.get(rows))
    if not glyphs or glyphs.pop() != ')':
        return None
    digits = []
    while glyphs:
        glyph = glyphs.pop()
        if glyph == ':' and digits:
            return int(''.join(reversed(digits)))
        if glyph not in ('0', '1'):
            return None
        digits.append(glyph)
    return None


def _palette(root, folder, count, sequence, recovery_frame=None):
    captured = native_visual.capture(root, folder / f'evec-{sequence}-window.bmp')
    recognized = native_visual.recognize(captured)
    headers = []
    for row in recognized['rows']:
        text = re.sub(r'\s+', '', row['text'])
        if '符' in text and '设' in text and '数' in text:
            headers.append(row)
    bitmap = native_visual.Bitmap(captured['path'])
    row = headers[0] if len(headers) == 1 else _caption_from_cancel(bitmap, recognized['rows'])
    center = int((row['box'][0] + row['box'][2]) / 2)
    y = int(row['box'][1] - 3)
    bright = lambda x, py: min(bitmap.pixel(x, py)) >= 120 and max(bitmap.pixel(x, py)) - min(bitmap.pixel(x, py)) < 25
    left = right = center
    while left > 0 and bright(left - 1, y):
        left -= 1
    while right + 1 < bitmap.width and bright(right + 1, y):
        right += 1
    width = right - left + 1
    scale = width / 396
    candidates = [py for py in range(max(0, row['box'][1] - 25), row['box'][1])
                  if bright(center, py) and bright(left + 5, py) and bright(right - 5, py)]
    if not .8 <= scale <= 2 or not candidates:
        raise IntegrationError('EVEC_UI_UNSUPPORTED', 'The note-settings frame does not match the validated layout.')
    top = min(candidates)
    height = round(488 * scale)
    if right >= bitmap.width or top + height > abs(bitmap.height):
        raise IntegrationError('EVEC_UI_UNSUPPORTED', 'The note-settings frame is clipped.')
    panel = native_visual.recognize(native_visual.capture(root, folder / f'evec-{sequence}-panel.bmp',
              crop=(left, top, width, height), scale=2))
    frame = {'left': left, 'top': top, 'scale': scale, 'width': width, 'height': height}
    if recovery_frame is not None:
        recovery_frame.update(frame)
    caption = ''.join(row['text'] for row in panel['rows'] if row['box'][1] < 48 * scale)
    numbers = re.findall(r'\d+', caption)
    actual_count = _caption_binary_count(panel['path'], scale)
    if actual_count is None:
        actual_count = int(numbers[-1]) if numbers else None
    if actual_count != count:
        raise IntegrationError('EVEC_SELECTION_UNVERIFIED', 'The enlarged actual caption does not confirm the requested complete part count.')
    normalize = lambda text: re.sub(r'[\W_]', '', text.lower())
    left_labels = [normalize(row['text']) for row in panel['rows']
                   if 240 * scale < row['box'][1] / 2 < 287 * scale and row['box'][2] / 2 < 210 * scale]
    right_labels = [normalize(row['text']) for row in panel['rows']
                    if 240 * scale < row['box'][1] / 2 < 287 * scale and row['box'][0] / 2 > 210 * scale]
    # Exact local OCR aliases for the two static column headings only.
    if not any('color' in label for label in left_labels) or not any(
            'release' in label or label in {'r引巳a3巳', 'vor巳巳3巳'} for label in right_labels):
        raise IntegrationError('EVEC_UI_UNSUPPORTED', 'Both EVEC column labels must be read before content input.')
    return frame


def _selected(root, frame, column, row, folder, sequence):
    capture = native_visual.capture(root, folder / f'evec-{sequence}-selection.bmp',
              crop=(frame['left'], frame['top'], frame['width'], frame['height']))
    bitmap = native_visual.Bitmap(capture['path'])
    scale = frame['scale']
    x = (50 if column == 'color' else 213) * scale
    y = (296 + 19 * row) * scale
    orange = 0
    for py in range(int(y - 5 * scale), int(y + 5 * scale) + 1):
        for px in range(int(x - 6 * scale), int(x + 6 * scale) + 1):
            red, green, blue = bitmap.pixel(px, py)
            orange += red > 160 and 65 < green < 210 and blue < 100 and red > green
    if orange < 3:
        raise IntegrationError('EVEC_CHOICE_UNVERIFIED', 'The actual EVEC selection checkmark was not verified; the dialog is not committed.')


def _clip_signature(data):
    project, chunks = ppsf.load(data)
    referenced = set()
    logical = {}
    for entry in expressions.native_entries(project, chunks):
        logical.setdefault(entry['clip_id'], []).append(entry['id'])
    def resolve(value, field=''):
        if field == 'event_indices':
            referenced.update(value)
            return [expressions.plain(chunks['Events'].data[i]) for i in value]
        if field == 'sub_event_indices':
            return [resolve(items, 'event_indices') for items in value]
        if isinstance(value, dict):
            return {k: resolve(v, k) for k, v in value.items() if not k.startswith('_') and k != 'size'}
        if isinstance(value, (list, tuple)):
            return [resolve(v) for v in value]
        return expressions.plain(value)
    clips = expressions.plain(chunks['Clips'].data)
    for clip in clips['vocaloid3_note_clips']:
        referenced.update(clip['data']['noteclip']['iclip']['event_indices'])
        clip['data']['noteclip']['iclip']['event_indices'] = []
        clip['logical_notes'] = logical.get(clip['index'], [])
    stable = resolve(clips)
    unused = [ppsf.model.PpsfEvent.build(event, version=project.version).hex()
              for i, event in enumerate(chunks['Events'].data) if i not in referenced]
    blocks = {c.magic: ppsf.model.PpsfChunk.build(c, version=project.version).hex() for c in project.chunks
              if c.magic not in ('Events', 'Clips', 'EditorDatas')}
    return {'blocks': blocks, 'clips': stable, 'unused_events': sorted(unused)}


def _verify(before, after, ids, color, release):
    if _clip_signature(before) != _clip_signature(after):
        raise IntegrationError('EVEC_READBACK_MISMATCH', 'Unrelated native chunks, clip data or automation events changed.')
    old = expressions.inspect_native(before)['notes']
    new = expressions.inspect_native(after)['notes']
    if [n['id'] for n in old] != [n['id'] for n in new]:
        raise IntegrationError('EVEC_READBACK_MISMATCH', 'Logical note identities/count changed.')
    for a, b in zip(old, new):
        for field in ('track_index', 'part_index', 'note_index', 'track_name', 'part_start_tick',
                      'start_tick', 'duration_tick', 'note_num', 'lyric', 'phoneme', 'style'):
            if a[field] != b[field]:
                raise IntegrationError('EVEC_READBACK_MISMATCH', f'Unrequested {field} change on {a["id"]}.')
        av, bv = a['vibrato'], b['vibrato']
        if abs(av['length_percent'] - bv['length_percent']) > .11:
            raise IntegrationError('EVEC_READBACK_MISMATCH', 'Vibrato duration changed.')
        for curve in ('depth_curve', 'rate_curve'):
            positions = {p['position'] for p in av[curve] + bv[curve]}
            if any(abs(expressions.sample(av[curve], p) - expressions.sample(bv[curve], p)) > 1 for p in positions):
                raise IntegrationError('EVEC_READBACK_MISMATCH', 'Vibrato envelope changed.')
        for field, requested, tag in [('color', color, 'CVV'), ('release', release, 'VSil')]:
            expected = requested if a['id'] in ids and requested is not None else a['evec'][field]
            if b['evec'][field] != expected:
                raise IntegrationError('EVEC_READBACK_MISMATCH', f'Actual {field} differs on {a["id"]}.')
        allowed = {tag for tag, value in [('CVV', color), ('VSil', release)] if a['id'] in ids and value is not None}
        for tag, value in a['evec']['native_records'].items():
            if tag not in allowed and b['evec']['native_records'].get(tag) != value:
                raise IntegrationError('EVEC_READBACK_MISMATCH', f'Unrequested EVEC {tag} changed.')
    p1, c1 = ppsf.load(before)
    p2, c2 = ppsf.load(after)
    for a, b in zip(expressions.native_entries(p1, c1), expressions.native_entries(p2, c2)):
        if a['id'] not in ids and expressions.plain(a['members']) != expressions.plain(b['members']):
            raise IntegrationError('EVEC_READBACK_MISMATCH', 'Unrequested native note payload changed.')
        old_editor = expressions.plain(a['editor'].data)
        new_editor = expressions.plain(b['editor'].data)
        for item in (old_editor, new_editor):
            item.pop('unknown_field', None)
            item.pop('int32_array', None)
            item['sub_array_2'] = [v for v in item['sub_array_2'] if not (
                a['id'] in ids and v.get('magic') == 'VocaloidEVECData' and
                ((color is not None and v['data']['type_name'] == 'CVV') or
                 (release is not None and v['data']['type_name'] == 'VSil')))]
        if old_editor != new_editor:
            raise IntegrationError('EVEC_READBACK_MISMATCH', 'Unrequested editor-note metadata changed.')
    return new


def set_part(track_index, part_index, expected_revision, color=None, release=None, allow_dialog_fallback=False):
    if not allow_dialog_fallback:
        raise IntegrationError('DIALOG_FALLBACK_REQUIRED', 'First EVEC generation uses a verified Piapro settings UI. Explicitly enable the fallback.')
    if color not in (None, 'none', 'soft', 'power') or release not in (None, 'none', 'short', 'long') or color is release is None:
        raise IntegrationError('INVALID_EVEC_CHOICE', 'Choose a supported Miku color and/or release setting.')
    if type(track_index) is not int or type(part_index) is not int or min(track_index, part_index) < 0:
        raise IntegrationError('INVALID_TRACK_INDEX', 'Use Piapro internal track and part indexes from expression readback.')
    with file_lock(state_dir() / 'live-daw'):
        folder = singers._live_folder()
        root = windows.piapro_root()
        snapshot, before = singers._capture(folder, 'evec-before')
        if expressions.revision(before) != expected_revision:
            raise IntegrationError('PIAPRO_STATE_CHANGED', 'Read note expressions again before EVEC changes.')
        project, chunks = ppsf.load(before)
        entries = expressions.native_entries(project, chunks)
        targets = [e for e in entries if e['track_index'] == track_index and e['part_index'] == part_index]
        editors = [t for t in chunks['EditorDatas'].data.track_datas if t.data.track_type == 3]
        active = [i for i, t in enumerate(editors) if t.data.state_flags & 2]
        if not targets or active != [track_index] or len(editors[track_index].data.clip_datas) != 1:
            raise IntegrationError('PIAPRO_PART_VIEW_REQUIRED', 'This bounded fallback requires the requested active track to contain one part, opened in the piano roll. Other layouts are refused.')
        voice = singers.inspect_native(before)['tracks'][track_index].get('singer', {})
        if voice.get('name') not in ('MIKU_V4X_Original_EVEC', 'MIKU_V4X_Soft_EVEC', 'MIKU_V4X_Solid_EVEC'):
            raise IntegrationError('EVEC_VOICE_UNSUPPORTED', 'Only the locally verified Miku V4X EVEC libraries are supported.')
        ids = {e['id'] for e in targets}
        originals = {n['id']: n for n in expressions.inspect_native(before)['notes'] if n['id'] in ids}
        for field, requested in [('color', color), ('release', release)]:
            if requested is None and len({n['evec'][field] for n in originals.values()}) > 1:
                raise IntegrationError('EVEC_UI_MIXED_SETTINGS_UNSUPPORTED', 'The host dialog cannot preserve mixed unrequested color/release choices. Use native compiled switches or specify both part choices explicitly.')
        for entry in targets:
            for event in entry['members']:
                vibrato = event.data.vibrato
                if (vibrato.handle_id != 0 or vibrato.depth_data != b'normal'
                        or vibrato.flag != 0 or vibrato.rate_data != b''):
                    raise IntegrationError('VIBRATO_PROFILE_UNSUPPORTED', 'EVEC host generation currently preserves only the verified Normal/Type1 vibrato profile.')
        committed = False
        frame = {}
        try:
            windows.notify_activation(root['handle'])
            _menu(root, '编辑 / 全选')
            time.sleep(.4)
            _menu(root, '音符 / 设置E.V.E.C.')
            time.sleep(.6)
            frame = _palette(root, folder, len(targets), 0, frame)
            for sequence, (field, value, labels) in enumerate([
                ('color', color, ['none', 'soft', 'power']), ('release', release, ['none', 'short', 'long'])], 1):
                if value is None:
                    continue
                index = labels.index(value)
                x = 93 if field == 'color' else 269
                point = (frame['left'] + x * frame['scale'], frame['top'] + (296 + 19 * index) * frame['scale'])
                _click(root, point)
                _selected(root, frame, field, index, folder, sequence)
            _click(root, (frame['left'] + 242 * frame['scale'], frame['top'] + 463 * frame['scale']))
            committed = True
            time.sleep(.4)
            _, generated = singers._capture(folder, 'evec-host-generated')
            # Piapro's shared settings dialog commits mixed style values and may
            # reshape custom vibrato. Restore only those unintended target-note
            # parameters through the verified native writer before final import.
            repairs = [{'id': n['id'], 'style': n['style'], 'vibrato': n['vibrato']} for n in originals.values()]
            corrected = expressions.edit_native(generated, repairs)
            generated_notes = {n['id']: n for n in expressions.inspect_native(corrected)['notes']}
            pronunciation_repairs = []
            inactive_repeats = {}
            for original in originals.values():
                actual = generated_notes[original['id']]
                if any(original['evec'][key] != actual['evec'][key] for key in ('pronunciation', 'consonant_repeat')) or original['phoneme'] != actual['phoneme']:
                    mode, repeat = original['evec']['pronunciation'], original['evec']['consonant_repeat']
                    repair = {'id': original['id'], 'evec_pronunciation': mode}
                    if mode != 'none' or repeat == 1:
                        repair['evec_consonant_repeat'] = repeat
                    else:
                        inactive_repeats[original['id']] = repeat - 1
                    pronunciation_repairs.append(repair)
            if pronunciation_repairs:
                corrected = expressions.edit_native(corrected, pronunciation_repairs)
            if inactive_repeats:
                cp, cc = ppsf.load(corrected)
                for entry in expressions.native_entries(cp, cc):
                    if entry['id'] in inactive_repeats:
                        entry['editor'].data.vibrato_start = inactive_repeats[entry['id']]
                corrected = ppsf.model.PpsfLegacyProject.build(cp)
            _verify(before, corrected, ids, color, release)
            staged = write_result(None, str(folder / 'evec-preserved-expression.ppsf'), corrected)
            windows._send_file('import_ppsf', staged['path'], 10, allow_replace=True)
            readback, after = singers._capture(folder, 'evec-readback')
            expressions.verify_native(corrected, after)
            records = _verify(before, after, ids, color, release)
        except Exception as exc:
            restored = False
            try:
                # A self-drawn palette is not a native modal dialog. Cancel its
                # current frame before importing the already verified snapshot.
                if not committed and frame:
                    _click(root, (frame['left'] + 330 * frame['scale'], frame['top'] + 463 * frame['scale']))
                    _, cancelled = singers._capture(folder, 'evec-cancelled-readback')
                    expressions.verify_native(before, cancelled)
                    restored = True
                if not restored:
                    windows._send_file('import_ppsf', snapshot['path'], 10, allow_replace=True)
                _, recovery = singers._capture(folder, 'evec-recovery')
                expressions.verify_native(before, recovery)
                restored = True
            except Exception:
                pass
            raise IntegrationError('EVEC_CHANGE_INCOMPLETE', f'{exc}; snapshot: {snapshot["path"]}; automatic restoration verified: {restored}. Inspect before retrying.') from exc
        return {'verified': True, 'live_state_updated': True, 'disk_project_saved': False,
                'method': 'bounded Piapro EVEC UI host generation and complete native readback',
                'scope': 'entire currently open part on a single-part active track',
                'snapshot': snapshot, 'readback': readback, 'revision': expressions.revision(after),
                'notes': [n for n in records if n['id'] in ids]}
