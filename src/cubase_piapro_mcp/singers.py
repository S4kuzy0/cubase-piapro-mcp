"""Singer metadata and guarded native PPSF program assignments.

Only public installation/preset metadata is read. Voicebank payloads and license
material are never opened. Native program layouts are accepted by exact shape.
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from . import ppsf
from .errors import IntegrationError
from .storage import digest, file_lock, read_file, state_dir, write_result


PROGRAM_PREFIX = bytes.fromhex("0000000000010000000100000000000000")


def installed_voicebanks():
    if os.name != "nt":
        return {"voicebanks": [], "available": False, "reason": "Windows registry required"}
    import winreg

    voices = {}
    for version in (3, 4):
        for database in ("DATABASE", "DATABASE41"):
            key_path = rf"SOFTWARE\VOCALOID{version}\{database}"
            try:
                root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0,
                                      winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
            except FileNotFoundError:
                continue
            with root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    component_id = winreg.EnumKey(root, index)
                    with winreg.OpenKey(root, component_id) as entry:
                        try:
                            name = winreg.QueryValueEx(entry, "NAME")[0]
                            installed = winreg.QueryValueEx(entry, "INSTALLED")[0]
                        except FileNotFoundError:
                            continue
                    if installed != 1 or not isinstance(name, str):
                        continue
                    voices[component_id] = {"component_id": component_id,
                                            "display_name": name, "engine_version": version,
                                            "installed_metadata": True,
                                            "activation_verified": False}
    return {"voicebanks": sorted(voices.values(), key=lambda item: item["display_name"]),
            "available": True, "source": "VOCALOID 3/4 installation registry metadata",
            "scope": "Installed metadata only; activation and Piapro compatibility require live verification"}


def singer_record(entry, index):
    raw = ppsf.model.PpsfProjectV3SingerEntry.build(entry)
    return {"id": "singer:" + digest(raw), "project_index": index,
            "name": entry.singer_name, "preset_name": entry.singer_id,
            "component_id": entry.voice_setting.name,
            "secondary_component_id": entry.extra_voice_setting.name
            if entry.has_extra_voice_setting else None}


def program_plugin(chunks, track):
    indices = track.data.base.v3_event_plugin_indices
    candidates = [plugin for plugin in chunks["Plugins"].data.vocaloid3_event_control_plugins
                  if plugin.index in indices and plugin.data.name == "Vocaloid3 Program Change"]
    if len(candidates) != 1:
        raise IntegrationError("SINGER_LAYOUT_UNSUPPORTED", "No unique native program-change plugin for this track.")
    plugin = candidates[0]
    blob = plugin.data.vfxc_blob.data
    if len(blob) != len(PROGRAM_PREFIX) + 4 or not blob.startswith(PROGRAM_PREFIX):
        raise IntegrationError("SINGER_LAYOUT_UNSUPPORTED", "The track has an unverified program layout or timed singer changes. No write is allowed.")
    singer_index = int.from_bytes(blob[-4:], "little")
    parameters = plugin.data.parameters
    clips = [clip for clip in chunks["Clips"].data.automation_clips
             if clip.index == plugin.data.self_clip_idx and clip.data.plugin_index == plugin.index]
    if (len(parameters) != 1 or parameters[0].marker != 0 or parameters[0].value != singer_index
            or len(clips) != 1 or clips[0].data.event_indices
            or any(clips[0].data.sub_event_indices)):
        raise IntegrationError("SINGER_LAYOUT_UNSUPPORTED", "Redundant program values disagree, or timed singer automation is present. No write is allowed.")
    return plugin, singer_index


def inspect_native(data):
    project, chunks = ppsf.load(data)
    if "Project" not in chunks or "Plugins" not in chunks:
        raise IntegrationError("SINGER_LAYOUT_UNSUPPORTED", "Native singer/project chunks are missing.")
    entries = chunks["Project"].data.impl.vocaloid3_singers
    singers = [singer_record(entry, index) for index, entry in enumerate(entries)]
    tracks = []
    for index, track in enumerate(chunks["Tracks"].data.vocaloid3_event_tracks.data):
        record = {"track_index": index, "name": track.data.name}
        try:
            plugin, singer_index = program_plugin(chunks, track)
            if not 0 <= singer_index < len(singers):
                raise IntegrationError("SINGER_LAYOUT_UNSUPPORTED", "Native program index is outside the project singer table.")
            record.update(singer=singers[singer_index], program_plugin_index=plugin.index,
                          assignment_readable=True)
        except IntegrationError as exc:
            record.update(assignment_readable=False, reason=str(exc))
        tracks.append(record)
    return {"singers": singers, "tracks": tracks,
            "binary_roundtrip_identical": ppsf.model.PpsfLegacyProject.build(project) == data}


def assign_native(data, track_index, singer_bytes):
    """Use an actual Piapro-exported singer entry; never synthesize voice settings."""
    project, chunks = ppsf.load(data)
    if ppsf.model.PpsfLegacyProject.build(project) != data:
        raise IntegrationError("PPSF_ROUNDTRIP_MISMATCH", "Native singer writes require an exact byte round-trip.")
    original_chunks = {chunk.magic: ppsf.model.PpsfChunk.build(chunk, version=project.version)
                       for chunk in project.chunks}
    tracks = chunks["Tracks"].data.vocaloid3_event_tracks.data
    if type(track_index) is not int or not 0 <= track_index < len(tracks):
        raise IntegrationError("INVALID_TRACK_INDEX", "Choose a Piapro internal track from the latest singer snapshot.")
    entry = ppsf.model.PpsfProjectV3SingerEntry.parse(singer_bytes)
    if ppsf.model.PpsfProjectV3SingerEntry.build(entry) != singer_bytes:
        raise IntegrationError("INVALID_SINGER_ENTRY", "Singer bytes are not an exact native entry.")
    plugin, old_index = program_plugin(chunks, tracks[track_index])
    if any(plugin.index in track.data.base.v3_event_plugin_indices
           for index, track in enumerate(tracks) if index != track_index):
        raise IntegrationError("SINGER_LAYOUT_UNSUPPORTED", "The program plugin is shared with another track.")
    entries = chunks["Project"].data.impl.vocaloid3_singers
    if not 0 <= old_index < len(entries):
        raise IntegrationError("SINGER_LAYOUT_UNSUPPORTED", "The current native singer index is invalid.")
    target = next((index for index, value in enumerate(entries)
                   if ppsf.model.PpsfProjectV3SingerEntry.build(value) == singer_bytes), None)
    if target is None:
        before_length = len(ppsf.model.PpsfProject.build(chunks["Project"].data))
        target = len(entries)
        entries.append(deepcopy(entry))
        chunks["Project"].size += len(ppsf.model.PpsfProject.build(chunks["Project"].data)) - before_length
    plugin.data.vfxc_blob.data = PROGRAM_PREFIX + target.to_bytes(4, "little")
    plugin.data.parameters[0].value = target
    result = ppsf.model.PpsfLegacyProject.build(project)
    reread, after = ppsf.load(result)
    if ppsf.model.PpsfLegacyProject.build(reread) != result:
        raise IntegrationError("PPSF_WRITE_INVALID", "Singer output failed its native round-trip.")
    for chunk in reread.chunks:
        if chunk.magic not in ("Project", "Plugins") and original_chunks[chunk.magic] != ppsf.model.PpsfChunk.build(chunk, version=reread.version):
            raise IntegrationError("PPSF_UNRELATED_CHANGE", f"Unexpected singer edit to {chunk.magic}.")
    return result


def remember(data):
    _, chunks = ppsf.load(data)
    for entry in chunks["Project"].data.impl.vocaloid3_singers:
        raw = ppsf.model.PpsfProjectV3SingerEntry.build(entry)
        path = state_dir() / "singers" / (digest(raw) + ".bin")
        with file_lock(path):
            if path.exists():
                if path.read_bytes() != raw:
                    raise IntegrationError("SINGER_CACHE_CHANGED", "A cached singer definition differs from its fingerprint.")
            else:
                # The enclosing singer lock avoids duplicate writers. write_result
                # uses a separate filename lock, so use an exclusive durable file.
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())


def cached_entry(singer_id):
    if not isinstance(singer_id, str) or not re.fullmatch(r"singer:[a-f0-9]{64}", singer_id):
        raise IntegrationError("INVALID_SINGER_ID", "Use an exact singer ID from piapro_list_singers.")
    path = state_dir() / "singers" / (singer_id[7:] + ".bin")
    if not path.is_file() or path.stat().st_size > 16384:
        raise IntegrationError("SINGER_NOT_CAPTURED", "No verified native singer definition is cached. Capture it from a local PPSF or choose it in Piapro once, then list singers.")
    raw = path.read_bytes()
    if digest(raw) != singer_id[7:]:
        raise IntegrationError("SINGER_CACHE_CHANGED", "The cached native singer failed its checksum.")
    entry = ppsf.model.PpsfProjectV3SingerEntry.parse(raw)
    if ppsf.model.PpsfProjectV3SingerEntry.build(entry) != raw:
        raise IntegrationError("SINGER_CACHE_CHANGED", "The cached native singer failed its checksum/round-trip.")
    return raw


def catalog():
    records = []
    for path in sorted((state_dir() / "singers").glob("*.bin")):
        raw = cached_entry("singer:" + path.stem)
        entry = ppsf.model.PpsfProjectV3SingerEntry.parse(raw)
        record = singer_record(entry, 0)
        record.pop("project_index")
        records.append(record)
    return records


def list_from_file(path):
    source, data, sha = read_file(path)
    result = inspect_native(data)
    remember(data)
    return result | {"path": str(source), "sha256": sha, "captured_singers": catalog(),
                     "installation": installed_voicebanks(), "live_state_read": False}


def set_file(path, output_path, expected_sha256, track_index, singer_id):
    from .projects import read_expected
    from pathlib import Path

    source, data, _ = read_expected(path, expected_sha256)
    if source.suffix.lower() != ".ppsf" or Path(output_path).suffix.lower() != ".ppsf":
        raise IntegrationError("FORMAT_MISMATCH", "Native singer edits require PPSF input and output.")
    result = assign_native(data, track_index, cached_entry(singer_id))
    return write_result(source, output_path, result, expected_sha256) | {
        "live_state_updated": False, "singers": inspect_native(result)}


def revision(data):
    project, _ = ppsf.load(data)
    return digest(b"".join(ppsf.model.PpsfChunk.build(chunk, version=project.version)
                           for chunk in project.chunks if chunk.magic != "EditorDatas"))


def _capture(folder, name):
    from . import windows

    exported = windows._export_file("ppsf", folder / (name + ".ppsf"))
    data = read_file(exported["path"])[1]
    return exported, data


def _live_folder():
    from . import live_midi, windows

    windows.piapro_root()
    host = live_midi.get_state()
    if not host.get("connected") or host["state"]["playing"] or host["state"]["recording"]:
        raise IntegrationError("TRANSPORT_NOT_STOPPED", "Singer operations require verified stopped Cubase feedback.")
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    folder = state_dir() / "singer-operations" / stamp
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def list_live():
    with file_lock(state_dir() / "live-daw"):
        exported, data = _capture(_live_folder(), "current")
        result = inspect_native(data)
        remember(data)
        return result | {"revision": revision(data), "snapshot": exported,
                         "captured_singers": catalog(), "installation": installed_voicebanks(),
                         "live_state_read": True, "method": "native PPSF export; no content clicks"}


def _verify_applied(before, after, track_index, singer_id):
    original = inspect_native(before)
    current = inspect_native(after)
    if len(current["tracks"]) != len(original["tracks"]):
        raise IntegrationError("SINGER_READBACK_MISMATCH", "Piapro track count changed.")
    for index, (old, new) in enumerate(zip(original["tracks"], current["tracks"])):
        expected = singer_id if index == track_index else old.get("singer", {}).get("id")
        if not expected or new.get("singer", {}).get("id") != expected:
            raise IntegrationError("SINGER_READBACK_MISMATCH", f"Unexpected singer on internal track {index}.")
    p1, c1 = ppsf.load(before)
    p2, c2 = ppsf.load(after)
    for name in c1:
        if name not in ("Project", "Plugins", "EditorDatas"):
            if name not in c2 or ppsf.model.PpsfChunk.build(c1[name], version=p1.version) != ppsf.model.PpsfChunk.build(c2[name], version=p2.version):
                raise IntegrationError("SINGER_READBACK_MISMATCH", f"Unexpected change to {name}; recovery is required.")
    # Import may renumber the singer table. Normalize only those program indices,
    # then require every other plugin byte and every other project setting intact.
    for old_track, new_track in zip(c1["Tracks"].data.vocaloid3_event_tracks.data,
                                    c2["Tracks"].data.vocaloid3_event_tracks.data):
        old_plugin, _ = program_plugin(c1, old_track)
        new_plugin, _ = program_plugin(c2, new_track)
        new_plugin.data.vfxc_blob.data = old_plugin.data.vfxc_blob.data
        new_plugin.data.parameters[0].value = old_plugin.data.parameters[0].value
    c2["Project"].data.impl.vocaloid3_singers = c1["Project"].data.impl.vocaloid3_singers
    c2["Project"].size = c1["Project"].size
    for name in ("Project", "Plugins"):
        if ppsf.model.PpsfChunk.build(c1[name], version=p1.version) != ppsf.model.PpsfChunk.build(c2[name], version=p2.version):
            raise IntegrationError("SINGER_READBACK_MISMATCH", f"Unrelated {name} settings changed on import.")
    return current


def set_live(track_index, singer_id, expected_revision):
    from . import windows

    singer_bytes = cached_entry(singer_id)
    entry = ppsf.model.PpsfProjectV3SingerEntry.parse(singer_bytes)
    installed = {voice["component_id"] for voice in installed_voicebanks()["voicebanks"]}
    required = {entry.voice_setting.name}
    if entry.has_extra_voice_setting:
        required.add(entry.extra_voice_setting.name)
    if not required <= installed:
        raise IntegrationError("VOICEBANK_NOT_INSTALLED", "The captured singer refers to voicebanks without local installed metadata. No import was sent.")
    with file_lock(state_dir() / "live-daw"):
        folder = _live_folder()
        snapshot, before = _capture(folder, "before")
        if revision(before) != expected_revision:
            raise IntegrationError("PIAPRO_STATE_CHANGED", "List singers again before changing the live song.")
        changed = assign_native(before, track_index, singer_bytes)
        staged = write_result(None, str(folder / "requested.ppsf"), changed)
        try:
            windows._send_file("import_ppsf", staged["path"], 10, allow_replace=True)
            readback, after = _capture(folder, "readback")
            result = _verify_applied(before, after, track_index, singer_id)
        except Exception as exc:
            restored = False
            try:
                windows._send_file("import_ppsf", snapshot["path"], 10, allow_replace=True)
                _, recovered = _capture(folder, "recovery-readback")
                restored = revision(recovered) == revision(before)
            except Exception:
                pass
            raise IntegrationError("SINGER_CHANGE_INCOMPLETE", f"{exc}; recovery snapshot: {snapshot['path']}; automatic restoration verified: {restored}. Inspect before retrying.") from exc
        remember(after)
        return {"verified": True, "live_state_updated": True, "track_index": track_index,
                "singer_id": singer_id, "revision": revision(after), "snapshot": snapshot,
                "readback": readback, "tracks": result["tracks"], "disk_project_saved": False,
                "method": "native PPSF singer/program edit, native import, complete score readback"}
