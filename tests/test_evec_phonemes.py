import os
from pathlib import Path

import pytest

from cubase_piapro_mcp import expressions, ppsf


@pytest.mark.parametrize('mode', ['mild', 'strong'])
@pytest.mark.parametrize('repeat', [1, 2, 3, 4])
def test_native_pronunciation_matches_all_host_captured_japanese_consonants(mode, repeat):
    location = os.environ.get('CP_TEST_EVEC_SELECTION')
    if not location:
        pytest.skip('Set CP_TEST_EVEC_SELECTION to the local host A/B fixture directory')
    root = Path(location)
    original = (root / 'unlocked3-baseline.ppsf').read_bytes()
    records = expressions.inspect_native(original)['notes']
    assert len(records) == 35
    modified = expressions.edit_native(original, [
        {'id': n['id'], 'evec_pronunciation': mode, 'evec_consonant_repeat': repeat} for n in records])
    project, chunks = ppsf.load(modified)
    host, host_chunks = ppsf.load((root / f'unlocked3-{mode}-{repeat}.ppsf').read_bytes())
    actual_entries = expressions.native_entries(project, chunks)
    expected_entries = expressions.native_entries(host, host_chunks)
    for actual, expected in zip(actual_entries, expected_entries):
        assert actual['members'][0].data.phoneme == expected['members'][0].data.phoneme
        assert expressions.plain(expressions.evec_records(actual)['CTop']) == expressions.plain(expressions.evec_records(expected)['CTop'])
        assert actual['editor'].data.vibrato_start == expected['editor'].data.vibrato_start
    for before, after in zip(records, expressions.inspect_native(modified)['notes']):
        assert before['style'] == after['style'] and before['vibrato'] == after['vibrato']
