"""Local stdio MCP server. All logging stays off stdout."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from . import __version__, projects, storage, live_midi, windows, cubase, project_templates, singers, routing, expressions, piapro_evec, expression_ranges, note_selection, host_controls, plugin_catalog, plugin_loading, interfaces

mcp = MCPServer(
    "cubase-piapro",
    instructions=(
        "Data-first Cubase/Piapro tools. Inspect files first and use their SHA-256 for writes. "
        "Prefer native PPSF/VSQX/MIDI file edits over UI operations. File edits do not update the live editor. "
        "CPR inspection is partial and read-only; its indexes are not live track IDs. "
        "Import makes a snapshot first, then requires export/readback verification. "
        "Live mixer controls apply only to the selected Cubase channel. "
        "List live tracks to obtain real host IDs and the current revision before track operations. "
        "Create projects from local standalone templates; never close an existing project implicitly. "
        "Piapro supports only one instance per computer: reuse the existing instance. "
        "Piapro singer track_index is an INTERNAL score track, separate from Cubase host UIDs. "
        "Routing uses an explicitly enabled bounded UI fallback and actual readback; reserved MCP ports cannot be music routes. "
        "Native note expression IDs are logical t#:p#:n# associations, distinct from physical PPSF e# IDs and Cubase UIDs. "
        "Prefer native expression writes. Range tools use explicit logical IDs or half-open part note-index ranges. "
        "First EVEC enable/disable uses explicitly opted-in temporary internal work tracks, merged and removed before full readback. "
        "Host EQ/send/plugin controls require fresh track and control revisions. Values are normalized, with actual host display text returned. "
        "Program parameters are read-only; plugin loading only adds a new instrument or uses a proven empty insert, never replaces an occupied slot. "
        "Do not claim success when verified/connected is false or an error is returned."
    ),
)
READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


def _preview_host_tool(annotations):
    # Keep the registered 0.5 tool surface until this host extension passes its
    # own copy-based acceptance. Test clients explicitly opt into the preview.
    return mcp.tool(annotations=annotations) if os.environ.get("CUBASE_PIAPRO_HOST_CONTROLS_PREVIEW") == "1" else lambda function: function


class NoteChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    start_tick: int | None = None
    duration_tick: int | None = None
    note_num: int | None = None
    velocity: int | None = None
    lyric: str | None = None
    phoneme: str | None = None


class HostControlChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: str
    value: float


class CurvePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tick: int
    value: int


class NewNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_tick: int
    duration_tick: int
    note_num: int
    velocity: int = 64
    lyric: str = "あ"
    phoneme: str = "a"


class ExpressionStyle(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    bend_depth: int | None = None
    bend_length: int | None = None
    decay: int | None = None
    accent: int | None = None
    opening: int | None = None
    velocity: int | None = None
    rise_portamento: bool | None = None
    fall_portamento: bool | None = None


class VibratoPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    position: float
    value: int


class VibratoExpression(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    length_percent: float | None = None
    depth_curve: list[VibratoPoint] | None = None
    rate_curve: list[VibratoPoint] | None = None


class ExpressionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    style: ExpressionStyle | None = None
    vibrato: VibratoExpression | None = None
    evec_color: str | None = None
    evec_release: str | None = None
    evec_pronunciation: str | None = None
    evec_consonant_repeat: int | None = None


class NoteExpressionChange(ExpressionSettings):
    id: str


class NoteRange(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    track_index: int
    part_index: int
    start_note_index: int = 0
    end_note_index: int | None = None


class NoteSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    note_ids: list[str] | None = None
    ranges: list[NoteRange] | None = None


@mcp.tool(annotations=READ)
def get_capabilities() -> dict:
    """List implemented operations and exact limitations without changing any app."""
    return {
        "version": __version__,
        "transport": "stdio",
        "file_editing": ["vsqx3", "vsqx4", "legacy-ppsf-notes-singers-expressions-evec", "standard-midi"],
        "curves": "VSQX PIT/PBS/DYN/BRE/BRI/CLE/GEN/OPE/POR",
        "cpr": "partial read-only",
        "native_ppsf_gate": "exact binary roundtrip and native tail preservation; notes change events/matching editor copies; singers change only the table and two verified program copies",
        "live_control": "selected channel and transport over MIDI Remote, only if live feedback is available",
        "live_tracks": "host-cache-1 handshake, protocol-3 cached host UIDs/names/types/plugin identity, selection, and single audio/MIDI/Piapro additions; first 64 supported mixer tracks",
        "project_creation": "byte-copy a registered local standalone CPR template into a new directory; open only when no project is already open",
        "piapro_loading": "reuse/select/open the single existing instance, or add its first instrument track",
        "piapro_ui": "native activation notices, dynamic Win32 menus and standard dialogs; no foreground or pointer input for file operations",
        "piapro_singers": "native PPSF table and synchronized program copies; cached actual singer definitions, optimistic live revision, snapshot/import/readback and recovery",
        "note_expression": "native PPSF bend/accent/decay/opening/velocity/portamento; Normal/Type1 vibrato duration/depth/rate envelopes; logical EVEC associations; Miku pronunciation/repeat and compiled soft/power, short/long switching",
        "evec_host_generation": "explicit bounded settings UI on verified disposable internal Miku work tracks; merge only selected notes across parts/tracks, remove work tracks, complete native readback/recovery",
        "evec_range_host_validation": "verified native multi-part ranges, mixed-setting work-track generation and both-off; actual registered SDK first/individual-off/both-off with constant vibrato; varying release-only vibrato refused",
        "expression_selection": "explicit logical note_ids or multiple zero-based part note-index ranges with exclusive end; max 4096 notes, 256 ranges, 16 host-generation groups",
        "miku_pronunciation_inventory": sorted(expressions.PRONUNCIATION_CONSONANTS | expressions.PRONUNCIATION_SINGLE_PHONEMES),
        "midi_routing": "Cubase 14 English Routing inspector; host UID authorization, local Windows OCR, verified menu/field readback; instrument MIDI input and MIDI-track input/output",
        "cubase_dialog_fallback": "explicit allow_dialog_fallback for validated Cubase 14 English instrument/input selectors: DPI-scaled dialog-relative pointer messages, native search edit and key messages",
        "focus_click_enabled": os.environ.get("CUBASE_PIAPRO_FOCUS_CLICK") == "1",
        "file_operations_require_foreground": False,
        "backups": True,
        "concurrency": "required source SHA-256 plus per-target cross-process locks",
        "limitations": [
            "No arbitrary native CPR writes",
            "No Piapro NT ZIP editing",
            "Note insertion/deletion requires VSQX",
            "No automatic claim of live state after a file edit",
            "Piapro cannot run multiple plugin instances on the same computer (E10)",
            "New project creation does not close an existing project; template plugins/voicebanks must already be installed",
            "Additions are live/unsaved; MIDI input selection has no independent host routing readback",
            "Routing is a UI fallback, not a native routing API; only recognized current routes are offered",
            "Dedicated MCP ports and unverified All MIDI Inputs are not selectable music routes",
            "Singer writes require exact native roundtrip, known static program layout and a captured local definition; no timed singer automation",
            "Piapro drops additional vibrato points inside release-only tails: work generation of release-only EVEC with varying envelopes is refused before mutation; keep color, disable both, or explicitly choose constant curves",
        ],
        "state_dir": str(storage.state_dir()),
    }


@mcp.tool(annotations=READ)
def inspect_project(
    path: str, include_notes: bool = False, offset: int = 0, limit: int = 200
) -> dict:
    """Read VSQX, legacy PPSF, MIDI or partial CPR. Returns current SHA-256; note pages have stable IDs for this hash."""
    return projects.inspect_project(path, include_notes, offset, limit)


@mcp.tool(annotations=WRITE)
def edit_notes(
    path: str, output_path: str, changes: list[NoteChange], expected_sha256: str
) -> dict:
    """Edit specified existing notes directly. Same-path overwrite requires the inspected hash and creates a durable backup. Other outputs must be new."""
    return projects.edit_notes(
        path,
        output_path,
        [change.model_dump(exclude_none=True) for change in changes],
        expected_sha256,
    )


@mcp.tool(annotations=WRITE)
def transpose_notes(
    path: str,
    output_path: str,
    semitones: int,
    expected_sha256: str,
    track_index: int | None = None,
) -> dict:
    """Transpose existing notes in a file or one track, preserving all unrelated data; live editor is not changed."""
    return projects.transpose(
        path, output_path, semitones, expected_sha256, track_index
    )


@mcp.tool(annotations=WRITE)
def set_control_curve(
    path: str,
    output_path: str,
    expected_sha256: str,
    track_index: int,
    part_index: int,
    controller: str,
    points: list[CurvePoint],
    mode: str = "replace",
) -> dict:
    """Replace/merge one VSQX controller curve. PIT uses -8192..8191; PBS 0..24; other controllers 0..127. Ticks are part-relative."""
    return projects.set_curve(
        path,
        output_path,
        expected_sha256,
        track_index,
        part_index,
        controller,
        [point.model_dump() for point in points],
        mode,
    )


@mcp.tool(annotations=READ)
def read_control_curve(
    path: str,
    track_index: int,
    part_index: int,
    controller: str,
    offset: int = 0,
    limit: int = 500,
) -> dict:
    """Read one VSQX controller curve as part-relative tick/value points, with source SHA-256 and pagination."""
    from . import vsqx
    from .errors import IntegrationError

    source, data, sha = storage.read_file(path)
    if source.suffix.lower() != ".vsqx" or offset < 0 or not 1 <= limit <= 2000:
        raise IntegrationError(
            "INVALID_CURVE_REQUEST",
            "Use a VSQX file, nonnegative offset, and limit from 1 to 2000.",
        )
    points = vsqx.read_curve(data, track_index, part_index, controller)
    return {
        "sha256": sha,
        "controller": controller,
        "point_count": len(points),
        "points": points[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(points) else None,
    }


@mcp.tool(annotations=WRITE)
def set_vsqx_singer(
    path: str,
    output_path: str,
    expected_sha256: str,
    track_index: int,
    part_index: int,
    voice_bank: int,
    voice_program: int,
) -> dict:
    """Set all singer events in one VSQX part to an existing voice-table entry. Does not install voicebanks or change the live editor."""
    from . import vsqx
    from .errors import IntegrationError

    source, data, _ = projects.read_expected(path, expected_sha256)
    if source.suffix.lower() != ".vsqx" or Path(output_path).suffix.lower() != ".vsqx":
        raise IntegrationError(
            "FORMAT_MISMATCH", "Singer changes currently require VSQX."
        )
    result = vsqx.set_singer(data, track_index, part_index, voice_bank, voice_program)
    return storage.write_result(source, output_path, result, expected_sha256) | {
        "live_state_updated": False
    }


@mcp.tool(annotations=WRITE)
def arrange_vsqx_notes(
    path: str,
    output_path: str,
    expected_sha256: str,
    additions: list[NewNote] | None = None,
    deletions: list[str] | None = None,
    track_index: int = 0,
    part_index: int = 0,
) -> dict:
    """Insert/delete VSQX notes directly. Additions inherit the first remaining note's style. IDs of deletions refer to the inspected input; additions use part-relative ticks."""
    return projects.arrange_notes(
        path,
        output_path,
        expected_sha256,
        [note.model_dump() for note in additions] if additions else None,
        deletions,
        track_index,
        part_index,
    )


@mcp.tool(annotations=WRITE)
def create_vsqx(
    voice_source_path: str,
    output_path: str,
    notes: list[NewNote],
    voice_bank: int = 0,
    voice_program: int = 0,
    tempo_bpm: float = 120.0,
    track_name: str = "Vocal",
) -> dict:
    """Create a new VSQX4 score using only the voice table from a local VSQX4. No music is copied. Select an existing voice bank/program; ticks are relative to the new part."""
    return projects.create_score(
        voice_source_path,
        output_path,
        [note.model_dump() for note in notes],
        voice_bank,
        voice_program,
        tempo_bpm,
        track_name,
    )


@mcp.tool(annotations=READ)
def list_recovery_copies(original_path: str | None = None) -> dict:
    """List up to 100 recorded recovery copies, optionally for an exact original path."""
    return {"backups": storage.list_backups(original_path)}


@mcp.tool(annotations=WRITE)
def restore_recovery_copy(
    backup_path: str, target_path: str, expected_current_sha256: str
) -> dict:
    """Restore a recorded backup to its original target, after checking current hash and backing up the current version."""
    return storage.restore_backup(backup_path, target_path, expected_current_sha256)


@mcp.tool(annotations=READ)
def daw_get_status() -> dict:
    """Read Cubase/Piapro windows and MIDI feedback status; no focus, clicks or mutations."""
    return {
        "windows": windows.windows() if os.name == "nt" else [],
        "midi": live_midi.get_state(),
    }


@mcp.tool(annotations=READ)
def piapro_list_menus() -> dict:
    """Read the actual open Piapro editor's menu names and enabled states; no hard-coded handles."""
    return {"menus": windows.list_menus()}


@mcp.tool(annotations=WRITE)
def piapro_export(kind: str, output_path: str, timeout_seconds: int = 30) -> dict:
    """Export current Piapro state as vsqx/ppsf/wav through native menu/dialog commands. WAV in VSTi exports the selected Piapro track. Output must be new. Optional title-bar focus fallback only; file is validated before success."""
    return windows.export_file(kind, output_path, timeout_seconds)


@mcp.tool(annotations=WRITE)
def piapro_import(
    path: str, expected_sha256: str, initialize_empty: bool = False
) -> dict:
    """Import an immutable score copy after exporting a native recovery snapshot. Export/readback is required. VSQX/MIDI may append tracks. initialize_empty permits a first import only when empty-menu state and stopped MIDI host feedback agree."""
    return windows.import_file(path, expected_sha256, allow_empty=initialize_empty)


@mcp.tool(annotations=WRITE)
def piapro_list_singers(path: str | None = None) -> dict:
    """List native singer fingerprints, Piapro INTERNAL track assignments and captured local profiles. With path, inspect/capture a legacy PPSF without UI. Without path, export the actual stopped live editor and return its revision. Installed metadata does not prove activation. Definitions contain settings, never voicebank audio."""
    return singers.list_from_file(path) if path is not None else singers.list_live()


@mcp.tool(annotations=WRITE)
def set_ppsf_singer(path: str, output_path: str, expected_sha256: str,
                    track_index: int, singer_id: str) -> dict:
    """Assign a captured native singer to ONE internal PPSF track, retaining all music and unrelated settings. Requires current file SHA-256 and singer ID from piapro_list_singers. No live update. Unknown/timed/shared program layouts are refused; same-path overwrite has a verified backup."""
    return singers.set_file(path, output_path, expected_sha256, track_index, singer_id)


@mcp.tool(annotations=WRITE)
def piapro_set_singer(track_index: int, singer_id: str, expected_revision: str) -> dict:
    """Change ONE Piapro INTERNAL track singer using a captured native definition. Use latest piapro_list_singers revision, not a Cubase UID/revision. Requires stopped host feedback; snapshots before change, imports native PPSF, verifies all track assignments and unrelated song/plugin data, attempts verified recovery on failure. Changes remain unsaved in Cubase; phonemes/lyrics stay unchanged."""
    return singers.set_live(track_index, singer_id, expected_revision)


@mcp.tool(annotations=WRITE)
def piapro_read_note_expression(path: str | None = None, offset: int = 0, limit: int = 100,
                                selection: NoteSelection | None = None) -> dict:
    """Read logical native PPSF note style, vibrato curves and EVEC assignments. With path, file-only; otherwise export actual stopped Piapro and return an expression revision. IDs t#:p#:n# belong to this snapshot, not physical e# events or Cubase UIDs. Vibrato curve positions span zero to one and values zero to 127. Returns a bounded page."""
    if offset < 0 or not 1 <= limit <= 1000:
        raise expressions.IntegrationError('INVALID_PAGINATION', 'Use a nonnegative offset and a limit from one to 1000.')
    result = expressions.read_native_file(path) if path is not None else expressions.list_live()
    if selection is not None:
        result = note_selection.filter_info(result, selection.model_dump(exclude_none=True))
    result['note_count'] = len(result['notes'])
    result['notes'] = result['notes'][offset:offset + limit]
    return result | {'offset': offset, 'limit': limit}


@mcp.tool(annotations=WRITE)
def edit_ppsf_note_expression(path: str, output_path: str, expected_sha256: str,
                              changes: list[NoteExpressionChange]) -> dict:
    """File-only native PPSF expression edits with exact source SHA-256. Styles: bend depth/length, decay, accent (0..100), opening/velocity (0..127), boolean portamento. Normal/Type1 vibrato supports length percent and depth/rate curves. Miku EVEC pronunciation none/mild/strong and repeat 1..4 support verified Japanese vowels/nasal and 29 consonants with a Japanese vowel; use get_capabilities for inventory. Existing compiled color soft/power and release short/long may switch; first enable/disable needs host generation. Unknown associations/profiles are refused. No live update."""
    return expressions.set_file(path, output_path, expected_sha256,
                                [c.model_dump(exclude_none=True) for c in changes])


@mcp.tool(annotations=WRITE)
def piapro_set_note_expression(changes: list[NoteExpressionChange], expected_revision: str) -> dict:
    """Apply native note expressions using the latest piapro_read_note_expression revision and logical IDs. No content clicks. Saves a real snapshot, imports a validated native edit, verifies complete musical/expression data and attempts verified recovery on failure. Native vibrato currently supports the captured Normal/Type1 profile. Changes remain unsaved in Cubase."""
    return expressions.set_live([c.model_dump(exclude_none=True) for c in changes], expected_revision)


@mcp.tool(annotations=WRITE)
def edit_ppsf_note_expression_range(path: str, output_path: str, expected_sha256: str,
                                    selection: NoteSelection, settings: ExpressionSettings) -> dict:
    """Apply uniform native settings to explicit logical IDs OR multiple internal track/part note-index ranges. Indexes are zero-based; end_note_index is exclusive, omitted end means the part end. Overlaps are deduplicated; missing/empty ranges are rejected. Full file SHA required. Styles, Normal/Type1 vibrato, verified Miku pronunciation/repeat and already compiled EVEC switches use native data. First color/release generation or disable requires the live range tool. Does not update the live editor."""
    return expression_ranges.set_file(path, output_path, expected_sha256,
        selection.model_dump(exclude_none=True), settings.model_dump(exclude_none=True))


@mcp.tool(annotations=WRITE)
def piapro_set_note_expression_range(selection: NoteSelection, settings: ExpressionSettings,
                                     expected_revision: str, allow_dialog_fallback: bool = False) -> dict:
    """Apply uniform settings only to explicit logical IDs OR multiple zero-based half-open part note-index ranges. Uses the latest whole-song expression revision. Native edits are preferred. Explicit fallback enables first Miku EVEC color/release generation or disable on disposable internal work tracks in the SAME Piapro instance, verifies singer/notes/active state, merges only targets and removes temporary content. No need to open/select user parts. Up to 4096 notes/256 ranges/16 groups; unknown phonemes/references/layouts refused. Release-only host generation with varying vibrato is refused because Piapro drops tail curve points; keep color, disable both or explicitly request constant curves. Full native readback/recovery; Cubase remains unsaved."""
    return expression_ranges.set_live(selection.model_dump(exclude_none=True),
        settings.model_dump(exclude_none=True), expected_revision, allow_dialog_fallback)


@mcp.tool(annotations=WRITE)
def piapro_set_part_evec(track_index: int, part_index: int, expected_revision: str,
                         color: str | None = None, release: str | None = None,
                         allow_dialog_fallback: bool = False) -> dict:
    """Generate/disable Miku EVEC through the bounded Piapro settings fallback. Scope: ALL notes of the requested CURRENTLY OPEN piano-roll part on the active SINGLE-PART track. Open that part first. Color none/soft/power; release none/short/long. Requires explicit fallback opt-in and latest expression revision. Verifies actual caption count, selected choices, native associations and all unrelated music; snapshots and verifies recovery. Existing compiled soft/power changes should prefer piapro_set_note_expression."""
    return piapro_evec.set_part(track_index, part_index, expected_revision, color, release, allow_dialog_fallback)


@mcp.tool(annotations=WRITE)
def cubase_get_midi_routing(track_id: str, expected_revision: str,
                            allow_dialog_fallback: bool = False) -> dict:
    """Select a fresh live host UID and read recognized MIDI routes through the Cubase 14 English Routing inspector. Explicit UI fallback required: local Windows OCR and bounded window-relative messages; no physical cursor. Requires stopped transport and an expanded supported inspector. Returns actual selected labels, route IDs, host_revision and routing revision. Instrument tracks expose input only; MIDI tracks expose both. This is UI readback, not a native routing API."""
    return routing.get_routing(track_id, expected_revision, allow_dialog_fallback)


@mcp.tool(annotations=WRITE)
def cubase_set_midi_routing(track_id: str, expected_revision: str, expected_routing_revision: str,
                            input_route_id: str | None = None, output_route_id: str | None = None,
                            allow_dialog_fallback: bool = False) -> dict:
    """Set current route IDs from cubase_get_midi_routing, with its host_revision and routing revision. Explicit bounded UI fallback required. Reopens menus to verify actual input/output and retains a recovery checkpoint. Reserved MCP ports and unverified All MIDI Inputs are blocked. MIDI output applies ONLY to MIDI tracks; never replaces an instrument slot. No automatic Cubase save. On failure inspect recovery status before retrying."""
    return routing.set_routing(track_id, expected_revision, expected_routing_revision,
                               input_route_id, output_route_id, allow_dialog_fallback)


@mcp.tool(annotations=WRITE)
def cubase_control(action: str, value: float | None = None) -> dict:
    """Control play/stop/record or selected channel volume/pan/mute/solo via MIDI Remote. Success requires host feedback. Record starts recording."""
    return live_midi.control(action, value)


@_preview_host_tool(READ)
def cubase_get_interfaces() -> dict:
    """Inspect this machine's actual Cubase executable version/API declarations and, when connected, controls-1 cached host metadata. Declared methods are separate from runtime verification. Does not activate DirectAccess, traverse native objects or load plugins."""
    return interfaces.inspect_interfaces()


@_preview_host_tool(READ)
def cubase_list_plugins() -> dict:
    """Read existing local Cubase scan/name caches, returning plugin IDs and a catalog revision. No DLL is loaded. Cached presence does not prove current availability/licensing; duplicate display names are not loadable through the fallback. No loaded class UID claim."""
    return plugin_catalog.list_plugins()


@_preview_host_tool(WRITE)
def cubase_get_controls(track_id: str, expected_revision: str, group: str = "summary") -> dict:
    """Select a fresh host UID and read cached standard MIDI Remote bindings. Groups: summary, eq, sends, instrument, insert.0 through insert.3. Four EQ bands; installed send slots up to eight; first 64 public parameters per supported plugin slot, with overflow reported. Values are normalized and include actual host display text. Send destination assignment is not provided. All Program selectors are read-only. Returns a control revision for writes; selecting a track may change the UI selection."""
    return host_controls.get_controls(track_id, expected_revision, group)


@_preview_host_tool(WRITE)
def cubase_set_controls(track_id: str, expected_revision: str, expected_control_revision: str,
                         group: str, changes: list[HostControlChange]) -> dict:
    """Write one to sixteen explicit IDs from the latest cubase_get_controls read, using normalized values. Binary controls accept zero/one. Requires stopped transport and automation write off; no Program changes. Native standard bindings run on mapping idle with cached UID/session/revision checks. Requests are never automatically resent. Verifies all controls in the group and attempts reread-verified recovery on failure. Cubase stays unsaved."""
    return host_controls.set_controls(track_id, expected_revision, expected_control_revision,
                                      group, [item.model_dump() for item in changes])


@_preview_host_tool(WRITE)
def cubase_load_plugin(plugin_id: str, expected_catalog_revision: str, expected_revision: str,
                        target: str, track_name: str | None = None, track_id: str | None = None,
                        insert_slot: int | None = None, expected_control_revision: str | None = None,
                        allow_dialog_fallback: bool = False) -> dict:
    """Load a current cached plugin ID into new_instrument or insert. Cubase 14 lacks the newer native assignment manager, so explicit bounded UI opt-in is required. Inserts require fresh summary control revision, an audio/instrument host UID and the FIRST EMPTY slot zero through three; occupied slots are never replaced. New instruments use a unique cached name; Piapro reuses its sole instance. Actual host name/vendor/version/format verifies loading, not loaded class UID or sound/library activation. Unknown initialization questions are left for the operator and never acknowledged automatically. Verified recovery/checkpoints; no implicit CPR save."""
    return plugin_loading.load_plugin(plugin_id, expected_catalog_revision, expected_revision, target,
        track_name=track_name, track_id=track_id, insert_slot=insert_slot,
        expected_control_revision=expected_control_revision, allow_dialog_fallback=allow_dialog_fallback)


@mcp.tool(annotations=READ)
def cubase_list_templates() -> dict:
    """List hash-verified local standalone CPR templates. Templates contain no bundled commercial plugins or voicebanks."""
    return project_templates.list_templates()


@mcp.tool(annotations=WRITE)
def cubase_create_project(
    template_id: str, project_directory: str, name: str | None = None,
    open_project: bool = True, timeout_seconds: int = 40,
) -> dict:
    """Create a native project by byte-copying a registered local template into a NEW absolute directory. Never replaces existing content or closes another project. Opening requires no currently open project. Check live_state_verified; startup/licensing may remain pending. Use cubase_load_piapro afterward to verify its editor."""
    return cubase.create_project(template_id, project_directory, name, open_project, timeout_seconds)


@mcp.tool(annotations=READ)
def cubase_list_tracks() -> dict:
    """Read actual live track IDs, names, plugin identities and selection via MIDI Remote. Returns the revision required by track writes. Scope: first 64 audio/instrument/sampler/MIDI channels, including hidden channels; excludes folders, inputs and outputs. CPR indexes cannot be substituted."""
    return live_midi.list_tracks()


@mcp.tool(annotations=WRITE)
def cubase_select_track(track_id: str, expected_revision: str) -> dict:
    """Select a live host ID from cubase_list_tracks. Reject a stale revision; requires repeated real selected-track feedback. No cursor operations."""
    with storage.file_lock(storage.state_dir() / "live-daw"):
        return live_midi.select_track(track_id, expected_revision)


@mcp.tool(annotations=WRITE)
def cubase_add_track(
    track_type: str, name: str, expected_revision: str,
    allow_dialog_fallback: bool = False,
) -> dict:
    """Add ONE audio, midi or piapro host track and verify its new UID/name. Requires fresh live revision and stopped transport. MIDI/Piapro require explicit allow_dialog_fallback for bounded Cubase 14 English dialog-relative selector messages; audio uses native edit/button messages. Existing Piapro is rejected to prevent E10. New tracks remain unsaved; checkpoint records original inventory/selection."""
    return cubase.add_track(track_type, name, expected_revision, allow_dialog_fallback)


@mcp.tool(annotations=WRITE)
def cubase_load_piapro(
    expected_revision: str, track_name: str = "Piapro",
    allow_dialog_fallback: bool = False,
) -> dict:
    """Reuse/select/open the single existing Piapro instance and verify its native editor/menus. If absent, add the FIRST Piapro instrument track; this requires explicit allow_dialog_fallback. Never replaces an existing instrument. Piapro's one-instance-per-computer limit also includes other DAWs."""
    return cubase.load_piapro(expected_revision, track_name, allow_dialog_fallback)


def main():
    logging.basicConfig(level=logging.WARNING)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
