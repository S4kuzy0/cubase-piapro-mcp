# Offline file-edit demo

After installing the package, run `python examples/offline_demo.py` from the repository root. It generates four new MIDI notes, reads their IDs and SHA-256, transposes a new copy and verifies the result plus the unchanged input. It never opens a DAW, queries installed singers, or reads personal files.

The default output is a new `work/offline-demo-*` directory. An explicit `--output` must also be a new directory. Generated MIDI files and the verification JSON are local artifacts, not bundled songs. MIDI pitch values are demonstration data; this example does not demonstrate synthesized audio or live Piapro acceptance.
