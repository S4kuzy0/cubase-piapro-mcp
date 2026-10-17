"""Host compilation on disposable internal tracks, merged into original native data."""

import time
from pathlib import Path

from . import evec_focus, evec_patch, expressions, piapro_evec, ppsf, singers, vsqx, windows
from .errors import IntegrationError


def original_phone(entry):
    values = [v.data.syllable_strings for v in entry['editor'].data.sub_array_1
              if v.magic == 'VocalSynthQuantizeSyllable']
    if len(values) != 1 or len(values[0]) != 4:
        raise IntegrationError('EVEC_LAYOUT_UNSUPPORTED', 'No unique original native syllable for work-track compilation.')
    return values[0][2]


def plan(data, ids, settings):
    project, chunks = ppsf.load(data)
    evec_patch.check_layout(chunks)
    voices = singers.inspect_native(data)['tracks']
    groups = {}
    for entry in expressions.native_entries(project, chunks):
        if entry['id'] not in ids:
            continue
        voice = voices[entry['track_index']].get('singer', {})
        current = expressions.evec_summary(entry)
        color = settings.get('evec_color', current['color'])
        release = settings.get('evec_release', current['release'])
        mode, repeat = current['pronunciation'], current['consonant_repeat']
        if (voice.get('name') not in expressions.MIKU_EVEC_VOICES
                or color not in ('none', 'soft', 'power') or release not in ('none', 'short', 'long')
                or mode not in ('none', 'mild', 'strong') or not 1 <= repeat <= 4):
            raise IntegrationError('EVEC_WORK_PROFILE_UNSUPPORTED', 'Only captured Miku EVEC voices and known settings can use host generation.')
        for event in entry['members']:
            v = event.data.vibrato
            if (v.handle_id != 0 or v.depth_data != b'normal' or v.flag != 0 or v.rate_data != b''):
                raise IntegrationError('VIBRATO_PROFILE_UNSUPPORTED', 'Work generation preserves only the verified Normal/Type1 profile.')
        phone = original_phone(entry)
        tokens = phone.split()
        if not (len(tokens) == 1 and tokens[0] in expressions.PRONUNCIATION_SINGLE_PHONEMES
                or len(tokens) == 2 and tokens[0] in expressions.PRONUNCIATION_CONSONANTS
                and tokens[1] in expressions.PRONUNCIATION_VOWELS):
            raise IntegrationError('EVEC_PHONEME_UNSUPPORTED', 'Work generation requires the verified original Japanese syllable inventory.')
        # Inactive repetition is retained in the original editor data; the
        # disabled host control uses one without affecting compiled timing.
        key = (voice['id'], color, release, mode, 1 if mode == 'none' else repeat)
        groups.setdefault(key, {'voice': voice, 'entries': []})['entries'].append(entry)
    if not groups or len(groups) > 16:
        raise IntegrationError('EVEC_WORK_GROUP_LIMIT', 'Use one to sixteen singer/color/release/pronunciation groups per request; split larger selections.')
    return list(groups.items())


def _voice_program(source, voice):
    tree, _ = vsqx.load(source)
    table = tree.getroot().find('{*}vVoiceTable')
    matches = [] if table is None else [v for v in vsqx.local(table, 'vVoice')
        if vsqx.text(v, 'id') == voice['component_id']
        and vsqx.text(v, 'id2', '') == (voice.get('secondary_component_id') or '')
        and vsqx.text(v, 'name') == voice['name']]
    if len(matches) != 1:
        raise IntegrationError('EVEC_WORK_VOICE_UNVERIFIED', 'Actual exported VSQX must contain a unique matching native voice.')
    return int(vsqx.text(matches[0], 'bs')), int(vsqx.text(matches[0], 'pc'))


def _work_score(source, group, name):
    bank, program = _voice_program(source, group['voice'])
    notes = [{'start_tick': 0, 'duration_tick': 1920, 'note_num': 60,
              'lyric': 'MCPFocus', 'phoneme': 'a', 'velocity': 64}]
    tick = 2400
    for entry in group['entries']:
        n = expressions.note_record(entry)
        notes.append({'start_tick': tick, 'duration_tick': n['duration_tick'], 'note_num': n['note_num'],
                      'lyric': n['lyric'], 'phoneme': original_phone(entry), 'velocity': n['style']['velocity']})
        tick += n['duration_tick'] + 480
    return vsqx.create(source, notes, bank, program, track_name=name), notes


def _work_entries(data, original, name, group, expected):
    project, chunks = ppsf.load(data)
    voices = singers.inspect_native(data)['tracks']
    original_count = len(singers.inspect_native(original)['tracks'])
    if len(voices) != original_count + 1 or voices[-1]['name'] != name or voices[-1].get('singer', {}).get('id') != group['voice']['id']:
        raise IntegrationError('EVEC_WORK_TRACK_UNVERIFIED', 'Imported temporary track/singer identity differs.')
    editors = [t for t in chunks['EditorDatas'].data.track_datas if t.data.track_type == 3]
    active = [i for i, t in enumerate(editors) if t.data.state_flags & 2]
    if active != [original_count] or len(editors[-1].data.clip_datas) != 1:
        raise IntegrationError('EVEC_WORK_TRACK_UNVERIFIED', 'Only the appended one-part work track may be active.')
    entries = expressions.native_entries(project, chunks)
    original_notes = expressions.inspect_native(original)['notes']
    previous = [expressions.note_record(e) for e in entries if e['track_index'] < original_count]
    if previous != original_notes:
        raise IntegrationError('EVEC_WORK_IMPORT_MISMATCH', 'Appending a work track changed original notes.')
    targets = [e for e in entries if e['track_index'] == original_count]
    if len(targets) != len(expected):
        raise IntegrationError('EVEC_WORK_IMPORT_MISMATCH', 'Work note count differs.')
    for entry, note in zip(targets, expected):
        record = expressions.note_record(entry)
        if any(record[k] != note[k] for k in ('start_tick', 'duration_tick', 'note_num', 'lyric')) or original_phone(entry) != note['phoneme']:
            raise IntegrationError('EVEC_WORK_IMPORT_MISMATCH', 'Actual work notes differ, including reinterpreted or locked phonemes.')
    return targets


def _apply(root, folder, count, sequence, color, release, mode, repeat, frame):
    windows.notify_activation(root['handle'])
    piapro_evec._menu(root, '编辑 / 打开钢琴卷帘')
    time.sleep(.3)
    evec_focus.focus_work_note(root, folder, sequence)
    piapro_evec._menu(root, '编辑 / 全选')
    time.sleep(.2)
    piapro_evec._menu(root, '音符 / 设置E.V.E.C.')
    time.sleep(.3)
    piapro_evec._palette(root, folder, count, sequence, frame)
    choices = [(93, {'none': 109, 'mild': 128, 'strong': 147}[mode]), (181 + 48 * (repeat - 1), 199),
               (93, 296 + 19 * ['none', 'soft', 'power'].index(color)),
               (269, 296 + 19 * ['none', 'short', 'long'].index(release))]
    for x, y in choices:
        piapro_evec._click(root, (frame['left'] + x * frame['scale'], frame['top'] + y * frame['scale']))
    for field, value, labels in [('color', color, ['none', 'soft', 'power']), ('release', release, ['none', 'short', 'long'])]:
        piapro_evec._selected(root, frame, field, labels.index(value), folder, f'{sequence}-{field}')
    piapro_evec._click(root, (frame['left'] + 242 * frame['scale'], frame['top'] + 463 * frame['scale']))
    frame.clear()
    time.sleep(.3)


def compile_native(base, ids, settings, folder, context):
    groups = plan(base, ids, settings)
    root = windows.piapro_root()
    voice_export = windows._export_file('vsqx', folder / 'work-voices.vsqx')
    source = Path(voice_export['path']).read_bytes()
    base_path = folder / f'work-base-{folder.name}.ppsf'
    base_path.write_bytes(base)
    compiled = {}
    for sequence, (key, group) in enumerate(groups):
        _, color, release, mode, repeat = key
        name = f'MCP_EVEC_WORK_{folder.name}_{sequence}'
        score, notes = _work_score(source, group, name)
        score_path = folder / f'work-{folder.name}-{sequence}.vsqx'
        score_path.write_bytes(score)
        context['attempted_path'] = base_path
        windows._send_file('import_ppsf', str(base_path), 10, allow_replace=True)
        context['attempted_path'] = score_path
        windows._send_file('import_vsqx', str(score_path), 10)
        _, imported = singers._capture(folder, f'work-{sequence}-imported')
        _work_entries(imported, base, name, group, notes)
        frame = context['frame']
        _apply(root, folder, len(notes), sequence, color, release, mode, repeat, frame)
        _, generated = singers._capture(folder, f'work-{sequence}-generated')
        targets = _work_entries(generated, base, name, group, notes)
        for original, actual in zip(group['entries'], targets[1:]):
            profile = expressions.evec_summary(actual)
            if any(profile[field] != value for field, value in [('color', color), ('release', release), ('pronunciation', mode), ('consonant_repeat', repeat)]):
                raise IntegrationError('EVEC_COMPILE_MISMATCH', 'Host-generated work expression differs from requested settings.')
            compiled[original['id']] = actual
    merged = evec_patch.merge(base, compiled)
    piapro_evec._verify(base, merged, set(ids), settings.get('evec_color'), settings.get('evec_release'))
    return merged, len(groups)
