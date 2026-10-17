import os
from pathlib import Path

import pytest

from cubase_piapro_mcp import evec_work, expressions, ppsf
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def lab():
    root = os.environ.get('CP_TEST_EVEC_SELECTION')
    if not root:
        pytest.skip('Set CP_TEST_EVEC_SELECTION to actual self-authored work-track captures')
    return Path(root)


def test_work_track_import_and_host_output_match_captured_native_inputs(lab):
    base = (lab / 'unlocked3-baseline.ppsf').read_bytes()
    ids = {'t2:p0:n6', 't2:p0:n9', 't2:p0:n10'}
    [(key, group)] = evec_work.plan(base, ids, {'evec_color': 'soft', 'evec_release': 'long'})
    source = (lab / 'scratch-demo.vsqx').read_bytes()
    _, expected = evec_work._work_score(source, group, 'MCP EVEC Work')
    for path in ('scratch-demo-imported.ppsf', 'scratch-demo-generated.ppsf'):
        targets = evec_work._work_entries((lab / path).read_bytes(), base, 'MCP EVEC Work', group, expected)
        assert len(targets) == 4 and targets[0]['members'][0].data.lyric == 'MCPFocus'


def test_work_track_rejects_lyric_phoneme_reinterpretation(lab):
    base = (lab / 'unlocked3-baseline.ppsf').read_bytes()
    [(key, group)] = evec_work.plan(base, {'t2:p0:n6', 't2:p0:n9', 't2:p0:n10'}, {'evec_color': 'soft'})
    _, expected = evec_work._work_score((lab / 'scratch-demo.vsqx').read_bytes(), group, 'MCP EVEC Work')
    expected[1]['phoneme'] = 'a'
    with pytest.raises(IntegrationError, match='EVEC_WORK_IMPORT_MISMATCH'):
        evec_work._work_entries((lab / 'scratch-demo-imported.ppsf').read_bytes(), base, 'MCP EVEC Work', group, expected)


def test_host_generation_groups_preserve_mixed_unrequested_pronunciation(lab):
    base = (lab / 'multi-native-changed.ppsf').read_bytes()
    ids = {n['id'] for n in expressions.inspect_native(base)['notes']}
    groups = evec_work.plan(base, ids, {'evec_color': 'soft'})
    assert {(key[3], key[4]) for key, group in groups} == {('strong', 2), ('none', 1)}
    assert sum(len(group['entries']) for key, group in groups) == 16


def acceptance():
    path = os.environ.get('CP_TEST_EVEC_ACCEPTANCE')
    if not path:
        pytest.skip('Set CP_TEST_EVEC_ACCEPTANCE to the local unlocked-desktop captures')
    return Path(path)


def test_color_off_with_varying_vibrato_is_refused_before_work_import():
    before = (acceptance() / 'multi-first.ppsf').read_bytes()
    with pytest.raises(IntegrationError, match='RELEASE_ONLY_VIBRATO_UNSUPPORTED'):
        evec_work.plan(before, {'t2:p0:n1'}, {'evec_color': 'none'})


def test_actual_host_discards_additional_release_only_tail_curve_points():
    wanted = (acceptance() / 'release-only-varying-requested.ppsf').read_bytes()
    host = (acceptance() / 'release-only-varying-host.ppsf').read_bytes()
    p, c = ppsf.load(wanted); q, d = ppsf.load(host)
    old = next(e for e in expressions.native_entries(p, c) if e['id'] == 't2:p0:n1')
    new = next(e for e in expressions.native_entries(q, d) if e['id'] == 't2:p0:n1')
    assert expressions.evec_summary(old)['color'] == 'none'
    assert len(old['members'][-1].data.vibrato.amplitude_bp) == 2
    assert len(new['members'][-1].data.vibrato.amplitude_bp) == 1
    with pytest.raises(IntegrationError, match='RELEASE_ONLY_VIBRATO_UNSUPPORTED'):
        expressions.edit_native(wanted, [{'id': old['id'], 'vibrato': expressions.native_vibrato(old)}])
