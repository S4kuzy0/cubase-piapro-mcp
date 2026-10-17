import os
from pathlib import Path

import pytest

from cubase_piapro_mcp import expressions, ppsf, storage
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def fixtures():
    root = os.environ.get('CP_TEST_EXPRESSIONS')
    if not root:
        pytest.skip('Set CP_TEST_EXPRESSIONS to the local self-authored native A/B fixtures')
    return Path(root)


def test_expression_edit_preserves_other_notes_and_all_unrelated_chunks(fixtures):
    before = (fixtures / 'baseline.ppsf').read_bytes()
    change = {'id': 't2:p0:n0', 'style': {'accent': 73, 'decay': 31, 'bend_depth': 19,
              'bend_length': 27, 'opening': 101, 'rise_portamento': True, 'fall_portamento': True},
              'vibrato': {'length_percent': 67,
                          'depth_curve': [{'position': 0, 'value': 20}, {'position': .5, 'value': 80}, {'position': 1, 'value': 40}],
                          'rate_curve': [{'position': 0, 'value': 35}, {'position': 1, 'value': 65}]}}
    changed = expressions.edit_native(before, [change])
    original = expressions.inspect_native(before)['notes']
    after = expressions.inspect_native(changed)['notes']
    assert after[1:] == original[1:]
    assert all(after[0]['style'][key] == value for key, value in change['style'].items())
    assert [p['value'] for p in after[0]['vibrato']['depth_curve']] == [20, 80, 40]
    assert [p['position'] for p in after[0]['vibrato']['depth_curve']] == pytest.approx([0, .5, 1], abs=.0000011)
    project, chunks = ppsf.load(changed)
    entry = expressions.native_entries(project, chunks)[0]
    assert entry['editor'].data.dur_tick_copy == 634
    assert entry['members'][0].data.vibrato.start_position == 1286
    old_project, old_chunks = ppsf.load(before)
    for name in old_chunks.keys() - {'Events', 'EditorDatas'}:
        assert ppsf.model.PpsfChunk.build(old_chunks[name], version=old_project.version) == ppsf.model.PpsfChunk.build(chunks[name], version=project.version)


def test_note_association_is_clip_local_across_multiple_tracks(fixtures):
    data = (fixtures / 'style-input-native.ppsf').read_bytes()
    records = expressions.inspect_native(data)['notes']
    assert records[0]['event_ids'] == [0]
    assert records[3]['event_ids'] == [3]
    changed = expressions.edit_native(data, [{'id': records[3]['id'], 'style': {'accent': 42}}])
    after = expressions.inspect_native(changed)['notes']
    assert after[:3] == records[:3]
    assert after[3]['style']['accent'] == 42


def test_evec_color_switch_synchronizes_metadata_and_compiled_vowel(fixtures):
    data = (fixtures / 'evec-soft.ppsf').read_bytes()
    original = expressions.inspect_native(data)['notes']
    assert len(original) == 3 and all(n['evec']['color'] == 'soft' for n in original)
    changed = expressions.edit_native(data, [{'id': original[1]['id'], 'evec_color': 'power'}])
    after = expressions.inspect_native(changed)['notes']
    assert after[0] == original[0] and after[2] == original[2]
    assert after[1]['evec']['color'] == 'power'
    project, chunks = ppsf.load(changed)
    entry = expressions.native_entries(project, chunks)[1]
    assert entry['members'][1].data.phoneme == 'a#6'
    assert len(changed) == len(data)
    assert sum(a != b for a, b in zip(data, changed)) == 2


def test_first_evec_enable_is_refused_without_host_generated_associations(fixtures):
    data = (fixtures / 'baseline.ppsf').read_bytes()
    with pytest.raises(IntegrationError, match='EVEC_UI_REQUIRED'):
        expressions.edit_native(data, [{'id': 't2:p0:n0', 'evec_color': 'soft'}])


@pytest.mark.parametrize('mode', ['mild', 'strong'])
@pytest.mark.parametrize('repeat', [1, 2, 3, 4])
def test_native_pronunciation_matches_host_compilation_without_ui_side_effects(fixtures, mode, repeat):
    data = (fixtures / 'evec-catalog-none-1.ppsf').read_bytes()
    original = expressions.inspect_native(data)['notes']
    target = next(n for n in original if n['track_index'] == 3)
    change = {'id': target['id'], 'evec_pronunciation': mode, 'evec_consonant_repeat': repeat}
    changed = expressions.edit_native(data, [change])
    after = expressions.inspect_native(changed)['notes']
    p1, c1 = ppsf.load(changed)
    p2, c2 = ppsf.load((fixtures / f'evec-catalog-{mode}-{repeat}.ppsf').read_bytes())
    actual = next(e for e in expressions.native_entries(p1, c1) if e['id'] == target['id'])
    host = next(e for e in expressions.native_entries(p2, c2) if e['id'] == target['id'])
    assert actual['members'][0].data.phoneme == host['members'][0].data.phoneme
    assert expressions.plain(expressions.evec_records(actual)['CTop']) == expressions.plain(expressions.evec_records(host)['CTop'])
    assert actual['editor'].data.vibrato_start == host['editor'].data.vibrato_start
    for old, new in zip(original, after):
        if old['id'] == target['id']:
            assert old['vibrato'] == new['vibrato'] and old['style'] == new['style']
        else:
            assert old == new


def test_first_pronunciation_enable_and_disable_preserve_container_sizes(fixtures):
    data = (fixtures / 'baseline.ppsf').read_bytes()
    changed = expressions.edit_native(data, [{'id': 't2:p0:n0', 'evec_pronunciation': 'mild', 'evec_consonant_repeat': 2}])
    assert expressions.inspect_native(changed)['notes'][0]['phoneme'] == 'k k k#2 a'
    restored = expressions.edit_native(changed, [{'id': 't2:p0:n0', 'evec_pronunciation': 'none', 'evec_consonant_repeat': 1}])
    assert restored == data


def test_compiled_release_switch_changes_only_its_two_synchronized_values(fixtures):
    data = (fixtures / 'release-short-final.ppsf').read_bytes()
    changed = expressions.edit_native(data, [{'id': 't2:p0:n0', 'evec_release': 'long'}])
    assert expressions.inspect_native(changed)['notes'][0]['evec']['release'] == 'long'
    assert len(changed) == len(data)
    assert sum(a != b for a, b in zip(data, changed)) == 2


def test_vibrato_projects_onto_evec_chain_without_changing_evec_or_music(fixtures):
    data = (fixtures / 'release-short-final.ppsf').read_bytes()
    original = expressions.inspect_native(data)['notes']
    change = {'id': original[0]['id'], 'vibrato': {'length_percent': 67,
              'depth_curve': [{'position': 0, 'value': 30}], 'rate_curve': [{'position': 0, 'value': 65}]}}
    after = expressions.inspect_native(expressions.edit_native(data, [change]))['notes']
    assert after[1:] == original[1:]
    assert after[0]['evec'] == original[0]['evec']
    assert all(point['value'] == 30 for point in after[0]['vibrato']['depth_curve'])
    assert after[0]['duration_tick'] == original[0]['duration_tick']


def test_evec_vibrato_segment_endpoints_stay_inside_the_host_interval(fixtures):
    data = (fixtures / 'release-short-final.ppsf').read_bytes()
    change = {'id': 't2:p0:n0', 'vibrato': {'length_percent': 67,
              'depth_curve': [{'position': 0, 'value': 20}, {'position': .5, 'value': 80}, {'position': 1, 'value': 40}]}}
    changed = expressions.edit_native(data, [change])
    project, chunks = ppsf.load(changed)
    note = expressions.native_entries(project, chunks)[0]
    for event in note['members']:
        for points in [event.data.vibrato.amplitude_bp, event.data.vibrato.frequency_bp]:
            assert all(p.position < 1000000 for p in points)


def test_reprojected_evec_vibrato_has_no_sub_tick_boundary_duplicates(fixtures):
    data = (fixtures / 'release-short-final.ppsf').read_bytes()
    change = {'id': 't2:p0:n0', 'vibrato': {'length_percent': 67,
              'depth_curve': [{'position': 0, 'value': 20}, {'position': .45, 'value': 89}, {'position': 1, 'value': 48}]}}
    first = expressions.edit_native(data, [change])
    readback = expressions.inspect_native(first)['notes'][0]['vibrato']
    second = expressions.edit_native(first, [{'id': change['id'], 'vibrato': readback}])
    project, chunks = ppsf.load(second)
    entry = expressions.native_entries(project, chunks)[0]
    for event in entry['members']:
        vibrato = event.data.vibrato
        if not vibrato.start_position:
            continue
        for points in [vibrato.amplitude_bp, vibrato.frequency_bp]:
            assert len({p.position for p in points}) == len(points)
            assert all(p.position == 0 or p.position >= 500000 / vibrato.start_position for p in points)
            assert all(p.position == 999999 or p.position <= 1000000 - 500000 / vibrato.start_position for p in points)


def test_pronunciation_refuses_a_bare_consonant(fixtures):
    project, chunks = ppsf.load((fixtures / 'baseline.ppsf').read_bytes())
    entry = expressions.native_entries(project, chunks)[1]
    syllable = next(v for v in entry['editor'].data.sub_array_1 if v.magic == 'VocalSynthQuantizeSyllable')
    syllable.data.syllable_strings[2] = 'k'  # Same encoded size as the original vowel.
    source = ppsf.model.PpsfLegacyProject.build(project)
    with pytest.raises(IntegrationError, match='EVEC_PHONEME_UNSUPPORTED'):
        expressions.edit_native(source, [{'id': entry['id'], 'evec_pronunciation': 'mild'}])


def test_pronunciation_does_not_guess_an_uncaptured_consonant(fixtures):
    project, chunks = ppsf.load((fixtures / 'baseline.ppsf').read_bytes())
    entry = expressions.native_entries(project, chunks)[0]
    syllable = next(v for v in entry['editor'].data.sub_array_1 if v.magic == 'VocalSynthQuantizeSyllable')
    syllable.data.syllable_strings[2] = 'z a'
    source = ppsf.model.PpsfLegacyProject.build(project)
    with pytest.raises(IntegrationError, match='EVEC_PHONEME_UNSUPPORTED'):
        expressions.edit_native(source, [{'id': entry['id'], 'evec_pronunciation': 'strong'}])


@pytest.mark.parametrize('change', [
    {'id': 'a', 'style': {'accent': 101}}, {'id': 'a', 'style': {'rise_portamento': 1}},
    {'id': 'a', 'vibrato': {'length_percent': float('nan')}},
    {'id': 'a', 'vibrato': {'depth_curve': [{'position': .1, 'value': 20}]}},
    {'id': 'a', 'vibrato': {'rate_curve': [{'position': 0, 'value': 20}, {'position': 0, 'value': 30}]}},
    {'id': 'a', 'vibrato': {'rate_curve': [{'position': 0, 'value': 128}]}},
])
def test_invalid_expression_values_are_rejected_before_native_parsing(change):
    with pytest.raises(IntegrationError):
        expressions.edit_native(b'not a native score', [change])


def test_source_hash_conflict_does_not_create_an_output(fixtures, tmp_path, monkeypatch):
    monkeypatch.setenv('CUBASE_PIAPRO_STATE_DIR', str(tmp_path / 'state'))
    output = tmp_path / 'refused.ppsf'
    with pytest.raises(IntegrationError, match='FILE_CHANGED'):
        expressions.set_file(str(fixtures / 'baseline.ppsf'), str(output), '0' * 64,
                             [{'id': 't2:p0:n0', 'style': {'accent': 42}}])
    assert not output.exists()


def test_expression_revision_includes_evec_metadata_and_ignores_selection(fixtures):
    data = (fixtures / 'evec-soft.ppsf').read_bytes()
    project, chunks = ppsf.load(data)
    entry = expressions.native_entries(project, chunks)[0]
    entry['editor'].data.unknown_field ^= 1
    selection = ppsf.model.PpsfLegacyProject.build(project)
    assert expressions.revision(selection) == expressions.revision(data)
    expressions.evec_records(entry)['CVV'].unknown_int = 105
    metadata = ppsf.model.PpsfLegacyProject.build(project)
    assert expressions.revision(metadata) != expressions.revision(data)
