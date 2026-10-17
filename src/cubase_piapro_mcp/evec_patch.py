"""Merge host-generated logical EVEC chains without rebuilding unrelated music."""

from collections import defaultdict

from construct import Container

from . import expressions, ppsf
from .errors import IntegrationError


def clone(value):
    if isinstance(value, dict):
        return Container({k: clone(v) for k, v in value.items() if not k.startswith('_')})
    if isinstance(value, (tuple, list)):
        return [clone(v) for v in value]
    return value


def check_layout(chunks):
    clips = chunks['Clips'].data
    unsupported = ('audio_clips', 'midi_note_clips', 'midi_sysex_clips', 'midi_meta_clips', 'vsq_note_clips')
    if any(clips[name] for name in unsupported):
        raise IntegrationError('EVEC_REMAP_LAYOUT_UNSUPPORTED', 'Host-generation merging currently supports native Vocaloid3 note and automation clips only; other clip references remain untouched.')
    count = len(chunks['Events'].data)
    for clip in clips.automation_clips:
        arrays = [clip.data.event_indices, *clip.data.sub_event_indices]
        if any(type(i) is not int or not 0 <= i < count for a in arrays for i in a):
            raise IntegrationError('EVEC_REMAP_LAYOUT_UNSUPPORTED', 'An automation event reference is not verified.')


def merge(base, compiled):
    """compiled maps ORIGINAL logical IDs to parsed, real host-generated entries."""
    project, chunks = ppsf.load(base)
    if ppsf.model.PpsfLegacyProject.build(project) != base:
        raise IntegrationError('PPSF_ROUNDTRIP_MISMATCH', 'EVEC merging requires an exact original native roundtrip.')
    check_layout(chunks)
    event_bytes = lambda values: len(ppsf.model.ppsf_prefixed_array(ppsf.model.PpsfEvent).build(values, version=project.version))
    old_event_bytes = event_bytes(chunks['Events'].data)
    old_clip_bytes = len(ppsf.model.PpsfClips.build(chunks['Clips'].data, version=project.version))
    entries = expressions.native_entries(project, chunks)
    lookup = {e['id']: e for e in entries}
    if not compiled or set(compiled) - lookup.keys():
        raise IntegrationError('INVALID_NOTE_ID', 'Every compiled replacement must map to one existing logical note.')
    original_records = {n['id']: n for n in expressions.inspect_native(base)['notes']}
    removed = {i for key in compiled for i in lookup[key]['event_ids']}
    for clip in chunks['Clips'].data.automation_clips:
        if any(i in removed for a in [clip.data.event_indices, *clip.data.sub_event_indices] for i in a):
            raise IntegrationError('EVEC_REMAP_LAYOUT_UNSUPPORTED', 'A replaced note event is shared with an automation clip.')
    replacements, slot_shapes = {}, {}
    for key, generated in compiled.items():
        original = lookup[key]
        en = original['editor'].data
        gn = generated['editor'].data
        if (gn.dur_tick != en.dur_tick or generated['members'][0].data.note != original['members'][0].data.note
                or sum(e.data.duration for e in generated['members']) != en.dur_tick):
            raise IntegrationError('EVEC_COMPILE_MISMATCH', 'Host compilation changed a target logical duration or pitch.')
        delta = en.pos_tick - gn.pos_tick
        members = [clone(event) for event in generated['members']]
        # Keep original words, manual phonemes, style/profile fields and all
        # unknown primary-note bytes. Only its compiled timing comes from host.
        primary = clone(original['members'][0])
        primary.data.tick = en.pos_tick
        primary.data.duration = members[0].data.duration
        members[0] = primary
        for event in members[1:]:
            event.data.tick += delta
        for event in members:
            old_body_size = len(ppsf.model.PpsfVocaloid3NoteEvent.build(event.data, version=project.version))
            event.data.vibrato.start_position = 0
            event.data.vibrato.amplitude_bp = []
            event.data.vibrato.frequency_bp = []
            event.size += len(ppsf.model.PpsfVocaloid3NoteEvent.build(event.data, version=project.version)) - old_body_size
        replacements[original['event_ids'][0]] = (key, members)
        slot_shapes[key] = [i != expressions.SENTINEL for i in gn.int32_array]
        old_size = original['editor'].size
        old_editor_body = len(ppsf.model.PpsfEditorNoteData.build(en, version=project.version))
        for tag in ('CVV', 'VSil'):
            old = [v for v in en.sub_array_2 if v.magic == 'VocaloidEVECData' and v.data.type_name == tag]
            new = [v for v in gn.sub_array_2 if v.magic == 'VocaloidEVECData' and v.data.type_name == tag]
            if len(old) != 1 or len(new) != 1:
                raise IntegrationError('EVEC_REMAP_LAYOUT_UNSUPPORTED', 'Color/release metadata must be uniquely associated.')
            en.sub_array_2[en.sub_array_2.index(old[0])] = clone(new[0])
        en.dur_tick_copy = en.dur_tick
        original['editor'].size += len(ppsf.model.PpsfEditorNoteData.build(en, version=project.version)) - old_editor_body
        size_delta = original['editor'].size - old_size
        original['_editor_clip'].size += size_delta
        original['_editor_track'].size += size_delta
        chunks['EditorDatas'].size += size_delta
    remap, new_ids, events = {}, {}, []
    for index, event in enumerate(chunks['Events'].data):
        if index in replacements:
            key, members = replacements[index]
            new_ids[key] = list(range(len(events), len(events) + len(members)))
            events.extend(members)
        elif index not in removed:
            remap[index] = len(events)
            events.append(event)
    chunks['Events'].data = events
    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry['clip_id']].append(entry)
    for clip in chunks['Clips'].data.vocaloid3_note_clips:
        old_body_size = len(ppsf.model.PpsfVocaloid3NoteClip.build(clip.data, version=project.version))
        indices = []
        for entry in grouped[clip.index]:
            key = entry['id']
            shape = slot_shapes.get(key, [i != expressions.SENTINEL for i in entry['editor'].data.int32_array])
            members = new_ids[key] if key in new_ids else [remap[i] for i in entry['event_ids']]
            cursor = len(indices)
            slots = []
            for present in shape:
                slots.append(cursor if present else expressions.SENTINEL)
                cursor += bool(present)
            entry['editor'].data.int32_array = slots
            indices.extend(members)
        clip.data.noteclip.iclip.event_indices = indices
        clip.size += len(ppsf.model.PpsfVocaloid3NoteClip.build(clip.data, version=project.version)) - old_body_size
    for clip in chunks['Clips'].data.automation_clips:
        clip.data.event_indices = [remap[i] for i in clip.data.event_indices]
        clip.data.sub_event_indices = [[remap[i] for i in a] for a in clip.data.sub_event_indices]
    chunks['Clips'].size += len(ppsf.model.PpsfClips.build(chunks['Clips'].data, version=project.version)) - old_clip_bytes
    # Native Events contains an unmodelled tail byte. Preserve existing padding
    # by applying the body delta, just as the other native editors do.
    chunks['Events'].size += event_bytes(events) - old_event_bytes
    result = ppsf.model.PpsfLegacyProject.build(project)
    repairs = [{'id': key, 'style': original_records[key]['style'], 'vibrato': original_records[key]['vibrato']}
               for key in compiled]
    return expressions.edit_native(result, repairs)
