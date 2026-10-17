import os
from pathlib import Path

import pytest
from construct import Container

from cubase_piapro_mcp import evec_patch, expressions, piapro_evec, ppsf
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def fixtures():
    root = os.environ.get('CP_TEST_EXPRESSIONS')
    if not root:
        pytest.skip('Set CP_TEST_EXPRESSIONS to local native reference files')
    return Path(root)


def entry(data, key):
    p, c = ppsf.load(data)
    return next(e for e in expressions.native_entries(p, c) if e['id'] == key)


def with_automation_reference(data, note_reference=False):
    project, chunks = ppsf.load(data)
    events = chunks['Events'].data
    index = 1 if note_reference else len(events)
    if not note_reference:
        events.append(Container(magic='AutomationEvent', size=9,
                                data=Container(tick=120, value=64, interpolation_type=0)))
    clip = chunks['Clips'].data.automation_clips[0]
    clip.data.event_indices = [index]
    clip.size = 2 + len(ppsf.model.PpsfAutomationClip.build(clip.data, version=project.version))
    chunks['Clips'].size = len(ppsf.model.PpsfClips.build(chunks['Clips'].data, version=project.version))
    chunks['Events'].size = len(ppsf.model.ppsf_prefixed_array(ppsf.model.PpsfEvent).build(events, version=project.version))
    return ppsf.model.PpsfLegacyProject.build(project)


def test_partial_evec_insertion_preserves_other_notes_and_remaps_automation(fixtures):
    before = with_automation_reference((fixtures / 'baseline.ppsf').read_bytes())
    generated = (fixtures / 'release-short-final.ppsf').read_bytes()
    key = 't2:p0:n1'
    merged = evec_patch.merge(before, {key: entry(generated, key)})
    piapro_evec._verify(before, merged, {key}, 'power', 'short')
    _, original_chunks = ppsf.load(before)
    original_count = len(original_chunks['Events'].data)
    p, c = ppsf.load(merged)
    assert len(c['Events'].data) == original_count + 2
    reference = c['Clips'].data.automation_clips[0].data.event_indices[0]
    assert reference == original_count + 1
    assert c['Events'].data[reference].data.value == 64
    assert expressions.inspect_native(merged)['notes'][2]['event_ids'] == [4]


def test_partial_evec_removal_keeps_remaining_chains_and_full_native_roundtrip(fixtures):
    before = (fixtures / 'release-short-final.ppsf').read_bytes()
    without = (fixtures / 'baseline.ppsf').read_bytes()
    key = 't2:p0:n1'
    merged = evec_patch.merge(before, {key: entry(without, key)})
    piapro_evec._verify(before, merged, {key}, 'none', 'none')
    p, c = ppsf.load(merged)
    assert ppsf.model.PpsfLegacyProject.build(p) == merged
    _, original_chunks = ppsf.load(before)
    assert len(c['Events'].data) == len(original_chunks['Events'].data) - 2
    notes = expressions.inspect_native(merged)['notes']
    assert notes[1]['event_ids'] == [3] and notes[2]['event_ids'] == [4, 5, 6]


def test_partial_evec_merge_refuses_shared_note_automation_reference(fixtures):
    before = with_automation_reference((fixtures / 'baseline.ppsf').read_bytes(), note_reference=True)
    generated = (fixtures / 'release-short-final.ppsf').read_bytes()
    with pytest.raises(IntegrationError, match='EVEC_REMAP_LAYOUT_UNSUPPORTED'):
        evec_patch.merge(before, {'t2:p0:n1': entry(generated, 't2:p0:n1')})


def test_partial_merge_preserves_native_event_chunk_tail_bytes(fixtures):
    before = (fixtures / 'baseline.ppsf').read_bytes()
    generated = (fixtures / 'release-short-final.ppsf').read_bytes()
    merged = evec_patch.merge(before, {'t2:p0:n1': entry(generated, 't2:p0:n1')})
    tails = []
    for data in (before, merged):
        project, chunks = ppsf.load(data)
        body = ppsf.model.ppsf_prefixed_array(ppsf.model.PpsfEvent).build(chunks['Events'].data, version=project.version)
        tails.append(chunks['Events'].size - len(body))
    assert tails[0] > 0 and tails[1] == tails[0]


def test_reference_guard_refuses_opaque_clip_families(fixtures):
    _, chunks = ppsf.load((fixtures / 'baseline.ppsf').read_bytes())
    chunks['Clips'].data.midi_note_clips.append(Container(data=b'opaque reference layout'))
    with pytest.raises(IntegrationError, match='EVEC_REMAP_LAYOUT_UNSUPPORTED'):
        evec_patch.check_layout(chunks)


def test_native_clone_preserves_binary_fields_without_copying_parser_streams():
    original = Container(data=b'\x00\xff', children=[Container(value=3, _io=object())], _io=object())
    copied = evec_patch.clone(original)
    assert copied.data == b'\x00\xff' and copied.children[0].value == 3
    assert '_io' not in copied and '_io' not in copied.children[0]
