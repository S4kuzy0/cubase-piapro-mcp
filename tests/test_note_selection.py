import pytest

from cubase_piapro_mcp import note_selection
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def info():
    parts = [{'track_index': 0, 'part_index': 0, 'note_count': 3},
             {'track_index': 0, 'part_index': 1, 'note_count': 2},
             {'track_index': 1, 'part_index': 0, 'note_count': 0}]
    notes = [{'id': f't0:p{pi}:n{ni}', 'track_index': 0, 'part_index': pi, 'note_index': ni}
             for pi, count in [(0, 3), (1, 2)] for ni in range(count)]
    return {'notes': notes, 'parts': parts}


def test_explicit_ids_follow_score_order_without_mutating_source(info):
    selected = note_selection.filter_info(info, {'note_ids': ['t0:p1:n1', 't0:p0:n0']})
    assert [n['id'] for n in selected['notes']] == ['t0:p0:n0', 't0:p1:n1']
    assert len(info['notes']) == 5 and selected['total_note_count'] == 5


def test_multiple_part_ranges_use_exclusive_end_and_deduplicate_overlap(info):
    result = note_selection.resolve(info, {'ranges': [
        {'track_index': 0, 'part_index': 0, 'start_note_index': 1, 'end_note_index': 3},
        {'track_index': 0, 'part_index': 1},
        {'track_index': 0, 'part_index': 0, 'start_note_index': 2}]})
    assert result == ['t0:p0:n1', 't0:p0:n2', 't0:p1:n0', 't0:p1:n1']


@pytest.mark.parametrize('selection', [
    {}, {'note_ids': []}, {'ranges': []}, {'all': True},
    {'note_ids': ['e0']}, {'note_ids': ['t0:p0:n0', 't0:p0:n0']},
    {'note_ids': ['t0:p0:n0'], 'ranges': [{'track_index': 0, 'part_index': 0}]},
    {'ranges': [{'track_index': 0, 'part_index': 9}]},
    {'ranges': [{'track_index': False, 'part_index': 0}]},
    {'ranges': [{'track_index': 0, 'part_index': 0, 'start_note_index': -1}]},
    {'ranges': [{'track_index': 0, 'part_index': 0, 'end_note_index': 4}]},
    {'ranges': [{'track_index': 0, 'part_index': 0, 'start_note_index': 2, 'end_note_index': 1}]},
    {'ranges': [{'track_index': 1, 'part_index': 0}]},
])
def test_selection_rejects_implicit_ambiguous_stale_and_empty_targets(info, selection):
    with pytest.raises(IntegrationError):
        note_selection.resolve(info, selection)
