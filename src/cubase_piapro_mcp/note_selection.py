"""Explicit logical-note selection; no DAW selection state or physical event IDs."""

from .errors import IntegrationError


def resolve(info, selection):
    if not isinstance(selection, dict) or set(selection) - {'note_ids', 'ranges'}:
        raise IntegrationError('INVALID_NOTE_SELECTION', 'Select explicit logical note_ids or part-index ranges.')
    ids, ranges = selection.get('note_ids'), selection.get('ranges')
    if bool(ids) == bool(ranges):
        raise IntegrationError('INVALID_NOTE_SELECTION', 'Provide exactly one nonempty note_ids or ranges list.')
    notes = info['notes']
    lookup = {n['id']: n for n in notes}
    selected = set()
    if ids:
        if (not isinstance(ids, list) or len(ids) > 4096 or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) != len(ids) or any(i not in lookup for i in ids)):
            raise IntegrationError('INVALID_NOTE_ID', 'Use unique current logical note IDs; every requested ID must exist.')
        selected.update(ids)
    else:
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 256:
            raise IntegrationError('INVALID_NOTE_SELECTION', 'Provide one to 256 part ranges.')
        parts = {(p['track_index'], p['part_index']): p for p in info['parts']}
        for span in ranges:
            required = {'track_index', 'part_index'}
            if (not isinstance(span, dict) or not required <= span.keys()
                    or set(span) - required - {'start_note_index', 'end_note_index'}):
                raise IntegrationError('INVALID_NOTE_SELECTION', 'A part range requires track_index and part_index; optional start/end note indexes are half-open.')
            ti, pi = span['track_index'], span['part_index']
            if type(ti) is not int or type(pi) is not int or (ti, pi) not in parts:
                raise IntegrationError('INVALID_PART_INDEX', 'Every requested internal track/part must exist in this snapshot.')
            count = parts[(ti, pi)]['note_count']
            start, end = span.get('start_note_index', 0), span.get('end_note_index', count)
            if type(start) is not int or type(end) is not int or not 0 <= start <= end <= count:
                raise IntegrationError('INVALID_NOTE_RANGE', 'Use zero-based note indexes with an exclusive end within the part.')
            selected.update(n['id'] for n in notes if n['track_index'] == ti and n['part_index'] == pi
                            and start <= n['note_index'] < end)
    if not selected:
        raise IntegrationError('NO_NOTES_SELECTED', 'The explicit selection contains no notes; no implicit whole-song selection is made.')
    if len(selected) > 4096:
        raise IntegrationError('NOTE_SELECTION_TOO_LARGE', 'A single operation supports at most 4096 logical notes.')
    return [n['id'] for n in notes if n['id'] in selected]


def summary(info, ids):
    selected = set(ids)
    parts = sorted({(n['track_index'], n['part_index']) for n in info['notes'] if n['id'] in selected})
    return {'note_ids': ids, 'note_count': len(ids), 'parts': [
        {'track_index': ti, 'part_index': pi} for ti, pi in parts],
        'scope': 'explicit logical IDs or zero-based half-open part note-index ranges'}


def filter_info(info, selection):
    ids = resolve(info, selection)
    selected = set(ids)
    return info | {'notes': [n for n in info['notes'] if n['id'] in selected],
                   'total_note_count': len(info['notes']), 'selection': summary(info, ids)}
