"""Verified note expression fields, preserving native EVEC event associations."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from . import ppsf, singers
from .errors import IntegrationError
from .storage import digest, file_lock, read_file, state_dir, write_result


STYLE_FIELDS = {
    'bend_depth': ('byte_25', 'bendDep', 100),
    'bend_length': ('byte_26', 'bendLen', 100),
    'decay': ('byte_28', 'decay', 100),
    'accent': ('byte_29', 'accent', 100),
    'opening': ('byte_30', 'opening', 127),
    'velocity': ('velocity', None, 127),
}
PORTAMENTO = {'rise_portamento': (1, 'risePort'), 'fall_portamento': (2, 'fallPort')}
COLOR_CODES = {101: 'soft', 105: 'power'}
RELEASE_CODES = {201: 'short', 202: 'long'}
PRONUNCIATION_CODES = {302: 'mild', 306: 'strong'}
MIKU_EVEC_VOICES = {'MIKU_V4X_Original_EVEC', 'MIKU_V4X_Soft_EVEC', 'MIKU_V4X_Solid_EVEC'}
SENTINEL = 0xFFFFFFFF
PRONUNCIATION_CONSONANTS = {
    'k', "k'", 'g', "g'", 's', 'S', 'ts', 'tS', 't', "t'", 'd', "d'",
    'dz', 'dZ', 'n', 'J', 'h', 'C', 'p\\', 'p', "p'", 'b', "b'", 'm', "m'",
    'j', '4', "4'", 'w',
}
PRONUNCIATION_VOWELS = {'a', 'i', 'M', 'e', 'o'}
PRONUNCIATION_SINGLE_PHONEMES = PRONUNCIATION_VOWELS | {'N\\'}


def plain(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items() if not k.startswith('_')}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def native_entries(project, chunks):
    if 'EditorDatas' not in chunks:
        raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Native editor-note associations are missing.')
    editors = [t for t in chunks['EditorDatas'].data.track_datas if t.data.track_type == 3]
    tracks = chunks['Tracks'].data.vocaloid3_event_tracks.data
    clips = {c.index: c for c in chunks['Clips'].data.vocaloid3_note_clips}
    if len(editors) != len(tracks):
        raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Native track/editor associations differ.')
    seen = set()
    result = []
    for ti, (track, editor) in enumerate(zip(tracks, editors)):
        if len(track.data.clip_indices) != len(editor.data.clip_datas):
            raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Native clip/editor associations differ.')
        for pi, (clip_id, ec) in enumerate(zip(track.data.clip_indices, editor.data.clip_datas)):
            clip = clips[clip_id].data.noteclip
            claimed = []
            for ni, en in enumerate(ec.data.element_array):
                if en.magic != 'EditorNoteData' or en.data.int32_array is None or len(en.data.int32_array) != 3:
                    raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Unknown note association layout.')
                offsets = [i for i in en.data.int32_array if i != SENTINEL]
                if not offsets or en.data.int32_array[0] == SENTINEL:
                    raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'No primary native note event.')
                if any(not 0 <= i < len(clip.iclip.event_indices) for i in offsets):
                    raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Invalid clip-local note association.')
                ids = [clip.iclip.event_indices[i] for i in offsets]
                members = []
                for index in ids:
                    if index in seen or not 0 <= index < len(chunks['Events'].data):
                        raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Shared or invalid expression event.')
                    event = chunks['Events'].data[index]
                    if event.magic != 'Vocaloid3NoteEvent':
                        raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Expression refers to a non-note event.')
                    members.append(event)
                    seen.add(index)
                if (en.data.pos_tick != members[0].data.tick
                        or not 0 <= en.data.dur_tick_copy <= en.data.dur_tick
                        or sum(e.data.duration for e in members) != en.data.dur_tick
                        or any(a.data.tick + a.data.duration != b.data.tick for a, b in zip(members, members[1:]))
                        or any(e.data.note != members[0].data.note for e in members)):
                    raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Native logical-note timing/pitch differs from its event chain.')
                claimed.extend(ids)
                result.append({'id': f't{ti}:p{pi}:n{ni}', 'track_index': ti, 'part_index': pi,
                               'note_index': ni, 'track_name': track.data.name,
                               'part_start_tick': clip.offset, 'editor': en, 'members': members,
                               '_editor_clip': ec, '_editor_track': editor,
                               'event_ids': ids, 'clip_id': clip_id})
            if claimed != list(clip.iclip.event_indices):
                raise IntegrationError('EXPRESSION_LAYOUT_UNSUPPORTED', 'Unclaimed/reordered native event associations.')
    return result


def evec_records(entry):
    return {v.data.type_name: v.data for v in entry['editor'].data.sub_array_2
            if v.magic == 'VocaloidEVECData'}


def evec_summary(entry):
    records = evec_records(entry)
    result = {}
    for field, tag, codes in [('color', 'CVV', COLOR_CODES), ('release', 'VSil', RELEASE_CODES)]:
        value = records.get(tag)
        result[field] = 'none' if value is None or value.unknown_int == SENTINEL else codes.get(value.unknown_int, 'unknown')
    result['native_records'] = plain(records)
    pronunciation = records.get('CTop')
    result['pronunciation'] = 'none' if pronunciation is None or pronunciation.unknown_int == SENTINEL else PRONUNCIATION_CODES.get(pronunciation.unknown_int, 'unknown')
    result['consonant_repeat'] = entry['editor'].data.vibrato_start + 1
    result['native_color_switch'] = result['color'] in ('soft', 'power')
    return result


def sample(points, position):
    if position <= points[0]['position']:
        return points[0]['value']
    for left, right in zip(points, points[1:]):
        if position <= right['position']:
            ratio = (position - left['position']) / (right['position'] - left['position'])
            return round(left['value'] + ratio * (right['value'] - left['value']))
    return points[-1]['value']


def native_vibrato(entry):
    ed = entry['editor'].data
    active = [event.data for event in entry['members'] if event.data.vibrato.start_position]
    if not active:
        return {'length_percent': 0.0, 'depth_curve': [{'position': 0.0, 'value': 64}],
                'rate_curve': [{'position': 0.0, 'value': 50}]}
    start = min(e.tick + e.duration - e.vibrato.start_position for e in active)
    length = ed.pos_tick + ed.dur_tick - start
    curves = {}
    for name, field in [('depth_curve', 'amplitude_bp'), ('rate_curve', 'frequency_bp')]:
        points = {}
        for event in active:
            vibrato = event.vibrato
            onset = event.tick + event.duration - vibrato.start_position
            for point in vibrato[field]:
                if not 0 <= point.value <= 127 or not 0 <= point.position <= 1000000:
                    raise IntegrationError('VIBRATO_LAYOUT_UNSUPPORTED', 'Vibrato control point is outside the verified native range.')
                position = (onset - start + point.position / 1000000 * vibrato.start_position) / length
                points[round(position, 9)] = point.value
        curves[name] = [{'position': p, 'value': v} for p, v in sorted(points.items())]
        if not curves[name]:
            raise IntegrationError('VIBRATO_LAYOUT_UNSUPPORTED', 'Active native vibrato has no control points.')
    return {'length_percent': length / ed.dur_tick * 100, **curves}


def note_record(entry):
    note = entry['members'][0].data
    editor = entry['editor'].data
    style = {name: note[field] for name, (field, _, _) in STYLE_FIELDS.items()}
    style.update({name: bool(note.byte_27 & mask) for name, (mask, _) in PORTAMENTO.items()})
    return {k: v for k, v in entry.items() if k not in ('editor', 'members') and not k.startswith('_')} | {
        'start_tick': editor.pos_tick, 'duration_tick': editor.dur_tick,
        'note_num': note.note, 'lyric': note.lyric, 'phoneme': note.phoneme,
        'style': style, 'vibrato': native_vibrato(entry), 'evec': evec_summary(entry)}


def inspect_native(data):
    project, chunks = ppsf.load(data)
    entries = native_entries(project, chunks)
    counts = Counter((e['track_index'], e['part_index']) for e in entries)
    clips = {c.index: c for c in chunks['Clips'].data.vocaloid3_note_clips}
    parts = []
    for ti, track in enumerate(chunks['Tracks'].data.vocaloid3_event_tracks.data):
        for pi, clip_id in enumerate(track.data.clip_indices):
            clip = clips[clip_id].data.noteclip
            parts.append({'id': f't{ti}:p{pi}', 'track_index': ti, 'part_index': pi,
                          'track_name': track.data.name, 'part_name': clip.iclip.name,
                          'part_start_tick': clip.offset, 'note_count': counts[(ti, pi)]})
    return {'format': 'ppsf-legacy', 'binary_roundtrip_identical': ppsf.model.PpsfLegacyProject.build(project) == data,
            'note_id_scope': 'logical editor notes t#:p#:n#; not physical e# events',
            'parts': parts, 'notes': [note_record(e) for e in entries]}


def validate_changes(changes):
    if not changes or len(changes) > 4096:
        raise IntegrationError('INVALID_EXPRESSION_CHANGE', 'Provide between one and 4096 logical note changes.')
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) - {'id', 'style', 'vibrato', 'evec_color', 'evec_release', 'evec_pronunciation', 'evec_consonant_repeat'}:
            raise IntegrationError('INVALID_EXPRESSION_CHANGE', 'Unknown note-expression field.')
        if not isinstance(change.get('id'), str) or change['id'] in seen:
            raise IntegrationError('INVALID_NOTE_ID', 'Use unique logical IDs from the latest expression readback.')
        seen.add(change['id'])
        if not any(change.get(k) for k in ('style', 'vibrato', 'evec_color', 'evec_release', 'evec_pronunciation', 'evec_consonant_repeat')):
            raise IntegrationError('EMPTY_EXPRESSION_CHANGE', 'Specify style, vibrato or a supported existing EVEC color switch.')
        for key, value in change.get('style', {}).items():
            if key in PORTAMENTO:
                valid = type(value) is bool
            else:
                valid = key in STYLE_FIELDS and type(value) is int and 0 <= value <= STYLE_FIELDS[key][2]
            if not valid:
                raise IntegrationError('INVALID_EXPRESSION_VALUE', f'Unsupported style value: {key}.')
        vibrato = change.get('vibrato', {})
        if set(vibrato) - {'length_percent', 'depth_curve', 'rate_curve'}:
            raise IntegrationError('INVALID_EXPRESSION_VALUE', 'Unknown vibrato parameter.')
        if 'length_percent' in vibrato:
            value = vibrato['length_percent']
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise IntegrationError('INVALID_EXPRESSION_VALUE', 'Vibrato length must be a finite percentage from zero to 100.')
        for field in ('depth_curve', 'rate_curve'):
            if field not in vibrato:
                continue
            points = vibrato[field]
            if not isinstance(points, list) or not 1 <= len(points) <= 1024:
                raise IntegrationError('INVALID_EXPRESSION_VALUE', 'Provide a nonempty bounded vibrato curve.')
            last = -1
            for point in points:
                if (not isinstance(point, dict) or set(point) != {'position', 'value'}
                        or type(point['position']) not in (int, float) or not math.isfinite(point['position'])
                        or not last < point['position'] <= 1 or point['position'] < 0
                        or type(point['value']) is not int or not 0 <= point['value'] <= 127):
                    raise IntegrationError('INVALID_EXPRESSION_VALUE', 'Curve positions must increase within zero to one; values are integers zero to 127.')
                last = point['position']
            if points[0]['position'] != 0:
                raise IntegrationError('INVALID_EXPRESSION_VALUE', 'A vibrato curve must start at position zero.')
        if change.get('evec_color') not in (None, 'soft', 'power'):
            raise IntegrationError('EVEC_UI_REQUIRED', 'First enable/disable EVEC using the verified host UI; native color switching accepts soft/power.')
        if change.get('evec_release') not in (None, 'short', 'long'):
            raise IntegrationError('EVEC_UI_REQUIRED', 'First enable/disable Voice Release using the host; compiled release switching accepts short/long.')
        if change.get('evec_pronunciation') not in (None, 'none', 'mild', 'strong'):
            raise IntegrationError('INVALID_EXPRESSION_VALUE', 'Miku pronunciation accepts none, mild or strong.')
        if 'evec_consonant_repeat' in change and (type(change['evec_consonant_repeat']) is not int or not 1 <= change['evec_consonant_repeat'] <= 4):
            raise IntegrationError('INVALID_EXPRESSION_VALUE', 'Consonant repeat is an integer from one to four.')


def _set_vibrato(entry, change):
    current = native_vibrato(entry)
    desired = current | change
    ed = entry['editor'].data
    length = round(ed.dur_tick * desired['length_percent'] / 100)
    # This historically named field is the logical vibrato onset, not a second duration.
    ed.dur_tick_copy = ed.dur_tick - length
    onset = ed.pos_tick + ed.dur_tick - length
    for event in entry['members']:
        note, vibrato = event.data, event.data.vibrato
        if (vibrato.handle_id != 0 or vibrato.depth_data != b'normal'
                or vibrato.flag != 0 or vibrato.rate_data != b''):
            raise IntegrationError('VIBRATO_PROFILE_UNSUPPORTED', 'Only the captured Normal/Type1 native profile is writable; other profiles remain intact.')
        local_onset = max(note.tick, onset)
        local_length = max(0, note.tick + note.duration - local_onset)
        if local_length > 65535:
            raise IntegrationError('VIBRATO_LENGTH_UNSUPPORTED', 'The native vibrato duration exceeds its verified 16-bit field.')
        vibrato.start_position = local_length
        for name, field in [('depth_curve', 'amplitude_bp'), ('rate_curve', 'frequency_bp')]:
            values = []
            if local_length:
                curve = desired[name]
                lower = (local_onset - onset) / length
                upper = (note.tick + note.duration - onset) / length
                values = [{'position': 0, 'value': sample(curve, lower)}]
                for point in curve:
                    if lower < point['position'] <= upper:
                        # Native EVEC regeneration discards an exact end-boundary
                        # point. Use the final representable point inside it.
                        position = min(999999, round((point['position'] - lower) / (upper - lower) * 1000000))
                        # Reading a segmented curve back introduces tiny float
                        # offsets at joins. Piapro merges sub-tick boundary
                        # points during EVEC compilation. Canonicalize those
                        # joins before writing, retaining strict host readback.
                        if position < 500000 / local_length:
                            position = 0
                        elif position > 1000000 - 500000 / local_length:
                            position = 999999
                        if position > values[-1]['position']:
                            values.append({'position': position, 'value': point['value']})
                        elif position == values[-1]['position']:
                            values[-1]['value'] = point['value']
                if upper < 1 and values[-1]['value'] != sample(curve, upper):
                    endpoint = {'position': 999999, 'value': sample(curve, upper)}
                    if values[-1]['position'] == 999999:
                        values[-1] = endpoint
                    else:
                        values.append(endpoint)
            if (event is entry['members'][-1] and ed.int32_array[1] == SENTINEL
                    and ed.int32_array[2] != SENTINEL and len(values) > 1):
                if len({point['value'] for point in values}) > 1:
                    raise IntegrationError('RELEASE_ONLY_VIBRATO_UNSUPPORTED', 'Piapro discards varying vibrato points inside a release-only tail. Keep Voice Color, disable both color and release, or explicitly request a constant envelope; no data was written.')
                values = values[:1]
            vibrato[field] = values


def _require_miku(chunks, entry):
    track = chunks['Tracks'].data.vocaloid3_event_tracks.data[entry['track_index']]
    _, index = singers.program_plugin(chunks, track)
    table = chunks['Project'].data.impl.vocaloid3_singers
    if not 0 <= index < len(table) or table[index].singer_name not in MIKU_EVEC_VOICES:
        raise IntegrationError('EVEC_VOICE_UNSUPPORTED', 'Native EVEC edits are limited to the verified Miku V4X EVEC libraries.')


def _set_pronunciation(chunks, entry, change):
    _require_miku(chunks, entry)
    current = evec_summary(entry)
    mode = change.get('evec_pronunciation', current['pronunciation'])
    repeat = change.get('evec_consonant_repeat', current['consonant_repeat'])
    if mode not in ('none', 'mild', 'strong') or not 1 <= repeat <= 4:
        raise IntegrationError('EVEC_LAYOUT_UNSUPPORTED', 'The current pronunciation/repeat profile is unknown.')
    if mode == 'none' and 'evec_consonant_repeat' in change and repeat != 1:
        raise IntegrationError('INVALID_EXPRESSION_VALUE', 'Enable mild/strong pronunciation before consonant repetition.')
    nodes = [v for v in entry['editor'].data.sub_array_2 if v.magic == 'VocaloidEVECData' and v.data.type_name == 'CTop']
    syllables = [v.data.syllable_strings for v in entry['editor'].data.sub_array_1 if v.magic == 'VocalSynthQuantizeSyllable']
    if len(nodes) != 1 or len(syllables) != 1 or len(syllables[0]) != 4 or nodes[0].data.parameter_name != '-1':
        raise IntegrationError('EVEC_LAYOUT_UNSUPPORTED', 'Original syllable or pronunciation metadata is not uniquely mapped.')
    node = nodes[0]
    if node.data.voicebank_name not in ('', 'MIKU_V4X_Original_EVEC'):
        raise IntegrationError('EVEC_LAYOUT_UNSUPPORTED', 'Pronunciation uses an uncaptured native EVEC settings bank.')
    phoneme = syllables[0][2]
    tokens = phoneme.split()
    if (len(tokens) not in (1, 2) or any('#' in token for token in tokens)
            or (len(tokens) == 1 and tokens[0] not in PRONUNCIATION_SINGLE_PHONEMES)
            or (len(tokens) == 2 and (tokens[0] not in PRONUNCIATION_CONSONANTS or tokens[1] not in PRONUNCIATION_VOWELS))):
        raise IntegrationError('EVEC_PHONEME_UNSUPPORTED', 'Use the verified Japanese consonant/vowel inventory. Uncaptured, custom and compound phonemes are preserved until separately verified.')
    old_length = len(ppsf.model.PpsfVocaloidEVECData.build(node.data))
    node.data.unknown_int = SENTINEL if mode == 'none' else (302 if mode == 'mild' else 306)
    node.data.voicebank_name = '' if mode == 'none' else 'MIKU_V4X_Original_EVEC'
    if 'evec_consonant_repeat' in change:
        entry['editor'].data.vibrato_start = repeat - 1
    if mode != 'none' and len(tokens) == 2:
        suffix = '#2' if mode == 'mild' else '#6'
        phoneme = ' '.join([tokens[0]] * repeat + [tokens[0] + suffix, tokens[1]])
    entry['members'][0].data.phoneme = phoneme
    delta = len(ppsf.model.PpsfVocaloidEVECData.build(node.data)) - old_length
    node.size += delta
    entry['editor'].size += delta
    entry['_editor_clip'].size += delta
    entry['_editor_track'].size += delta
    chunks['EditorDatas'].size += delta


def edit_native(data, changes):
    validate_changes(changes)
    project, chunks = ppsf.load(data)
    if ppsf.model.PpsfLegacyProject.build(project) != data:
        raise IntegrationError('PPSF_ROUNDTRIP_MISMATCH', 'Expression writes require an exact native byte roundtrip.')
    original = {c.magic: ppsf.model.PpsfChunk.build(c, version=project.version) for c in project.chunks}
    entries = {e['id']: e for e in native_entries(project, chunks)}
    events_model = ppsf.model.ppsf_prefixed_array(ppsf.model.PpsfEvent)
    old_events_length = len(events_model.build(chunks['Events'].data, version=project.version))
    old_sizes = {id(e): len(ppsf.model.PpsfVocaloid3NoteEvent.build(e.data, version=project.version))
                 for entry in entries.values() for e in entry['members']}
    for change in changes:
        if change['id'] not in entries:
            raise IntegrationError('INVALID_NOTE_ID', 'The requested logical note is not in this source snapshot.')
        entry = entries[change['id']]
        for name, value in change.get('style', {}).items():
            members = entry['members'][:1] if name in ('accent', 'decay') else entry['members']
            for event in members:
                if name in PORTAMENTO:
                    mask = PORTAMENTO[name][0]
                    event.data.byte_27 = (event.data.byte_27 & ~mask) | (mask if value else 0)
                else:
                    event.data[STYLE_FIELDS[name][0]] = value
        if change.get('vibrato'):
            _set_vibrato(entry, change['vibrato'])
        if change.get('evec_color'):
            _require_miku(chunks, entry)
            record = evec_records(entry).get('CVV')
            slot = entry['editor'].data.int32_array[1]
            if (record is None or record.unknown_int not in COLOR_CODES
                    or record.voicebank_name != 'MIKU_V4X_Original_EVEC' or slot == SENTINEL):
                raise IntegrationError('EVEC_UI_REQUIRED', 'First enabling EVEC requires native host generation of its derived events. No data was written.')
            color_member = 1  # Slot zero is the primary; slot one is the CVV vowel.
            derived = entry['members'][color_member].data
            old_suffix = '#2' if record.unknown_int == 101 else '#6'
            if not derived.lyric.startswith('EVEC(') or derived.phoneme not in [v + old_suffix for v in ('a', 'i', 'M', 'e', 'o')]:
                raise IntegrationError('EVEC_LAYOUT_UNSUPPORTED', 'The compiled EVEC vowel is not a verified Miku color chain.')
            suffix = '#2' if change['evec_color'] == 'soft' else '#6'
            derived.phoneme = derived.phoneme[:-2] + suffix
            record.unknown_int = 101 if change['evec_color'] == 'soft' else 105
        if change.get('evec_release'):
            _require_miku(chunks, entry)
            record = evec_records(entry).get('VSil')
            offset = entry['editor'].data.int32_array[2]
            if (record is None or record.unknown_int not in RELEASE_CODES or offset == SENTINEL
                    or record.voicebank_name != 'MIKU_V4X_Original_EVEC' or record.parameter_name != '-1'):
                raise IntegrationError('EVEC_UI_REQUIRED', 'First enabling Voice Release needs the host-generated tail event.')
            tail = entry['members'][-1].data
            if not tail.lyric.startswith('EVEC(') or tail.phoneme not in ('*#1', '*#2'):
                raise IntegrationError('EVEC_LAYOUT_UNSUPPORTED', 'The compiled Miku release event is not verified.')
            tail.phoneme = '*#1' if change['evec_release'] == 'short' else '*#2'
            record.unknown_int = 201 if change['evec_release'] == 'short' else 202
        if 'evec_pronunciation' in change or 'evec_consonant_repeat' in change:
            _set_pronunciation(chunks, entry, change)
    for entry in entries.values():
        for event in entry['members']:
            event.size += len(ppsf.model.PpsfVocaloid3NoteEvent.build(event.data, version=project.version)) - old_sizes[id(event)]
            if event.size > 65535:
                raise IntegrationError('NOTE_TOO_LARGE', 'Native expression payload exceeds its size field.')
    chunks['Events'].size += len(events_model.build(chunks['Events'].data, version=project.version)) - old_events_length
    result = ppsf.model.PpsfLegacyProject.build(project)
    reread, after = ppsf.load(result)
    if ppsf.model.PpsfLegacyProject.build(reread) != result:
        raise IntegrationError('PPSF_WRITE_INVALID', 'Native expression output did not roundtrip.')
    for name in original.keys() - {'Events', 'EditorDatas'}:
        if original[name] != ppsf.model.PpsfChunk.build(after[name], version=reread.version):
            raise IntegrationError('PPSF_UNRELATED_CHANGE', f'Expression edit changed unrelated {name}.')
    return result


def revision(data):
    project, chunks = ppsf.load(data)
    parts = [ppsf.model.PpsfChunk.build(c, version=project.version) for c in project.chunks if c.magic != 'EditorDatas']
    editors = []
    for entry in native_entries(project, chunks):
        value = plain(entry['editor'].data)
        value.pop('unknown_field', None)  # Current editor selection is not music.
        editors.append(value)
    return digest(b''.join(parts) + json.dumps(editors, sort_keys=True, ensure_ascii=False).encode())


def verify_native(expected, actual):
    if revision(expected) != revision(actual):
        raise IntegrationError('EXPRESSION_READBACK_MISMATCH', 'Complete native music/expression readback differs; recovery is required.')
    return inspect_native(actual)


def read_native_file(path):
    source, data, sha = read_file(path)
    return inspect_native(data) | {'path': str(source), 'sha256': sha, 'live_state_read': False}


def set_file(path, output_path, expected_sha256, changes):
    from .projects import read_expected
    source, data, _ = read_expected(path, expected_sha256)
    if source.suffix.lower() != '.ppsf' or Path(output_path).suffix.lower() != '.ppsf':
        raise IntegrationError('FORMAT_MISMATCH', 'This native expression tool requires legacy PPSF input and output.')
    result = edit_native(data, changes)
    inspected = inspect_native(result)
    selected = {change['id'] for change in changes}
    inspected['note_count'] = len(inspected['notes'])
    inspected['notes'] = [n for n in inspected['notes'] if n['id'] in selected]
    return write_result(source, output_path, result, expected_sha256) | {'live_state_updated': False,
        'expressions': inspected}


def list_live():
    with file_lock(state_dir() / 'live-daw'):
        snapshot, data = singers._capture(singers._live_folder(), 'expression-current')
        return inspect_native(data) | {'snapshot': snapshot, 'revision': revision(data), 'live_state_read': True}


def set_live(changes, expected_revision):
    from . import windows
    validate_changes(changes)
    with file_lock(state_dir() / 'live-daw'):
        folder = singers._live_folder()
        snapshot, before = singers._capture(folder, 'expression-before')
        if revision(before) != expected_revision:
            raise IntegrationError('PIAPRO_STATE_CHANGED', 'Read note expressions again before changing the live song.')
        desired = edit_native(before, changes)
        staged = write_result(None, str(folder / 'expression-requested.ppsf'), desired)
        try:
            windows._send_file('import_ppsf', staged['path'], 10, allow_replace=True)
            readback, actual = singers._capture(folder, 'expression-readback')
            verified = verify_native(desired, actual)
        except Exception as exc:
            restored = False
            try:
                windows._send_file('import_ppsf', snapshot['path'], 10, allow_replace=True)
                _, recovered = singers._capture(folder, 'expression-recovery')
                verify_native(before, recovered)
                restored = True
            except Exception:
                pass
            raise IntegrationError('EXPRESSION_CHANGE_INCOMPLETE', f'{exc}; recovery snapshot: {snapshot["path"]}; automatic restoration verified: {restored}. Inspect before retrying.') from exc
        return {'verified': True, 'live_state_updated': True, 'disk_project_saved': False,
                'snapshot': snapshot, 'readback': readback, 'revision': revision(actual),
                'note_count': len(verified['notes']),
                'notes': [n for n in verified['notes'] if n['id'] in {c['id'] for c in changes}]}
