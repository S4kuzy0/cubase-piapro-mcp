"""Generate and verify a synthetic MIDI edit without a DAW or personal score."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import uuid

import mido
from cubase_piapro_mcp import projects


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or Path(__file__).resolve().parents[1] / 'work' / ('offline-demo-' + uuid.uuid4().hex)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    song = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    song.tracks.append(track)
    track.append(mido.MetaMessage('set_tempo', tempo=500000))
    for pitch in [60, 64, 67, 72]:
        track.extend([mido.Message('note_on', note=pitch, velocity=72),
                      mido.Message('note_off', note=pitch, time=480)])
    source = output / 'synthetic.mid'
    song.save(source)
    original = source.read_bytes()
    before = projects.inspect_project(str(source), include_notes=True)
    changed = projects.transpose(str(source), str(output / 'transposed.mid'), 2, before['sha256'])
    after = projects.inspect_project(changed['path'], include_notes=True)
    assert [n['note_num'] for n in after['notes']] == [62, 66, 69, 74]
    assert source.read_bytes() == original
    assert hashlib.sha256(original).hexdigest() == before['sha256']
    report = {'source_unchanged': True, 'notes_before': before['note_count'],
              'notes_after': after['note_count'], 'live_state_updated': changed['live_state_updated'],
              'source_sha256': before['sha256'], 'output_sha256': after['sha256']}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), **report}, indent=2))


if __name__ == '__main__':
    main()
