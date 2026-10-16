# Third-party notices

## LibreSVIP binary PPSF model

Source: https://github.com/SoulMelody/LibreSVIP

Pinned commit: `98d2e5cd84eb139bb611b8c9159d996962c5d1c9`.

`libresvip/plugins/ppsf/legacy_model.py` is vendored as `_vendor/libresvip_legacy/model.py` without functional modifications. MIT license and copyright notice are included beside it. Only the binary schema is reused; the general lossy Project conversion pipeline and the upstream MCP server are not included.

## CubaseTools

Source: https://github.com/schwifty00/CubaseTools

Pinned commit: `b525d6c0e6d01a6ec13c3c4d3401c7c7ac25155d`.

The `cubasetools/core` parser components and package initializer are vendored with the MIT license. The wrapper replaces name-based final track deduplication. GUI and cleanup operations are not exposed through this MCP.

## UtaFormatix VSQX template

Source: https://github.com/sdercolin/utaformatix3/blob/master/core/src/main/resources/format_templates/template.vsqx

Template blob: `1af31cbe468dabd1b6f8872654835fa9a66955df`.

Copyright 2020 sdercolin. Apache License, Version 2.0. The template and license are included under `assets/`. New scores replace the template voice table with a user-supplied local voice table and generate new note data. No user song or voicebank is distributed.

## Runtime dependencies

The official MCP Python SDK, lxml, mido, python-rtmidi, construct and construct-typing are installed as dependencies, with their upstream licenses. Versions are fixed in the tested dependency snapshot. No code from the alternative AGPL yoka server is included.

## Steinberg interface reference

The original MIDI Remote bridge uses the documented interfaces at https://github.com/steinbergmedia/midiremote_api_doc . The documentation was consulted as an API reference; no Steinberg implementation or documentation source is vendored. Cubase, Piapro, commercial plugins, voicebanks and local CPR templates are not distributed with this project.
