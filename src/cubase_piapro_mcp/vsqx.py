"""Targeted VSQX 3/4 edits without a lossy intermediate song model."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from lxml import etree as ET

from .errors import IntegrationError

V4 = {
    "start_tick": "t",
    "duration_tick": "dur",
    "note_num": "n",
    "velocity": "v",
    "lyric": "y",
    "phoneme": "p",
}
V3 = {
    "start_tick": "posTick",
    "duration_tick": "durTick",
    "note_num": "noteNum",
    "velocity": "velocity",
    "lyric": "lyric",
    "phoneme": "phnms",
}
CONTROLLERS = {
    "PIT": "P",
    "PBS": "S",
    "DYN": "D",
    "BRE": "B",
    "BRI": "R",
    "CLE": "C",
    "GEN": "G",
    "OPE": "O",
    "POR": "T",
}


def load(data: bytes):
    parser = ET.XMLParser(
        resolve_entities=False,
        no_network=True,
        strip_cdata=False,
        remove_blank_text=False,
    )
    tree = ET.fromstring(data, parser).getroottree()
    if tree.docinfo.doctype:
        raise IntegrationError(
            "UNSUPPORTED_XML", "DTD and external entities are not supported."
        )
    root = tree.getroot()
    version = ET.QName(root).localname
    if version not in ("vsq3", "vsq4"):
        raise IntegrationError("INVALID_VSQX", "Expected a vsq3 or vsq4 document.")
    return tree, V3 if version == "vsq3" else V4


def local(element, name):
    return element.findall(f"{{*}}{name}")


def text(element, name, default=""):
    found = element.find(f"{{*}}{name}")
    return found.text if found is not None and found.text is not None else default


def parts(tree):
    for ti, track in enumerate(local(tree.getroot(), "vsTrack")):
        for pi, part in enumerate(
            list(local(track, "vsPart")) + list(local(track, "musicalPart"))
        ):
            yield ti, pi, track, part


def notes(tree, fields):
    rows = []
    for ti, pi, track, part in parts(tree):
        for ni, note in enumerate(local(part, "note")):
            row = {
                "id": f"t{ti}:p{pi}:n{ni}",
                "track_index": ti,
                "part_index": pi,
                "track_name": text(track, "name", text(track, "trackName")),
                "part_start_tick": int(text(part, "t", text(part, "posTick", "0"))),
            }
            for key, tag in fields.items():
                value = text(note, tag)
                row[key] = value if key in ("lyric", "phoneme") else int(value or 0)
            row["absolute_tick"] = row["part_start_tick"] + row["start_tick"]
            rows.append((row, note))
    return rows


def inspect(data: bytes, include_notes=False):
    tree, fields = load(data)
    rows = [row for row, _ in notes(tree, fields)]
    root = tree.getroot()
    voices = []
    table = root.find("{*}vVoiceTable")
    if table is not None:
        for voice in local(table, "vVoice"):
            voices.append(
                {
                    "name": text(voice, "name", text(voice, "vVoiceName")),
                    "bank": int(text(voice, "bs", text(voice, "vBS", "0"))),
                    "program": int(text(voice, "pc", text(voice, "vPC", "0"))),
                }
            )
    result = {
        "format": ET.QName(root).localname,
        "track_count": len(local(root, "vsTrack")),
        "part_count": sum(1 for _ in parts(tree)),
        "note_count": len(rows),
        "voices": voices,
        "ticks_per_beat": int(root.findtext("{*}masterTrack/{*}resolution", "480")),
    }
    master = root.find("{*}masterTrack")
    if master is not None:
        result["pre_measure"] = int(text(master, "preMeasure", "0"))
        result["tempos"] = [
            {
                "tick": int(text(t, "t", text(t, "posTick", "0"))),
                "bpm": int(text(t, "v", text(t, "bpm", "12000"))) / 100,
            }
            for t in local(master, "tempo")
        ]
    result["parts"] = [
        {
            "track_index": ti,
            "part_index": pi,
            "name": text(part, "name", text(part, "partName")),
            "start_tick": int(text(part, "t", text(part, "posTick", "0"))),
            "singers": [
                {
                    "tick": int(text(s, "t", text(s, "posTick", "0"))),
                    "bank": int(text(s, "bs", text(s, "vBS", "0"))),
                    "program": int(text(s, "pc", text(s, "vPC", "0"))),
                }
                for s in local(part, "singer")
            ],
        }
        for ti, pi, _, part in parts(tree)
    ]
    if include_notes:
        result["notes"] = rows
    return result


def validate_change(change: dict):
    ranges = {
        "start_tick": (0, 2**31 - 1),
        "duration_tick": (1, 2**31 - 1),
        "note_num": (0, 127),
        "velocity": (0, 127),
    }
    if "id" not in change:
        raise IntegrationError(
            "NOTE_ID_REQUIRED", "Use the note id returned by list_notes."
        )
    for key, value in change.items():
        if key == "id":
            continue
        if key not in V4:
            raise IntegrationError("UNSUPPORTED_FIELD", key)
        if key in ranges:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not ranges[key][0] <= value <= ranges[key][1]
            ):
                raise IntegrationError("INVALID_NOTE", f"Invalid {key}: {value}")
        elif not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
            raise IntegrationError("INVALID_NOTE", f"Invalid {key}")


def dump(tree):
    return ET.tostring(tree, encoding="utf-8", xml_declaration=True, pretty_print=False)


def edit(data: bytes, changes: list[dict]) -> bytes:
    tree, fields = load(data)
    lookup = {row["id"]: element for row, element in notes(tree, fields)}
    seen = set()
    touched = set()
    for change in changes:
        validate_change(change)
        key = change["id"]
        if key in seen or key not in lookup:
            raise IntegrationError(
                "INVALID_NOTE_ID", f"Missing or repeated note id: {key}"
            )
        seen.add(key)
        note = lookup[key]
        touched.add(note.getparent())
        for field, value in change.items():
            if field == "id":
                continue
            element = note.find(f"{{*}}{fields[field]}")
            if element is None:
                raise IntegrationError(
                    "MISSING_FIELD",
                    f"{field} is absent in {key}; no schema fields are guessed.",
                )
            element.text = (
                ET.CDATA(value) if field in ("lyric", "phoneme") else str(value)
            )
    for part in touched:
        ordered = sorted(
            local(part, "note"), key=lambda n: int(text(n, fields["start_tick"]))
        )
        for note in ordered:
            part.remove(note)
        plane = part.find("{*}plane")
        position = part.index(plane) if plane is not None else len(part)
        for note in ordered:
            part.insert(position, note)
            position += 1
        duration = part.find("{*}playTime")
        if duration is not None:
            end = max(
                int(text(n, fields["start_tick"]))
                + int(text(n, fields["duration_tick"]))
                for n in ordered
            )
            duration.text = str(max(int(duration.text or 0), end))
    result = dump(tree)
    inspect(result)
    return result


def curve(
    data: bytes,
    track_index: int,
    part_index: int,
    controller: str,
    points: list[dict],
    mode="replace",
):
    tree, fields = load(data)
    selected = [
        p for ti, pi, _, p in parts(tree) if (ti, pi) == (track_index, part_index)
    ]
    if (
        len(selected) != 1
        or controller not in CONTROLLERS
        or mode not in ("replace", "merge")
    ):
        raise IntegrationError(
            "INVALID_CURVE",
            "Choose an existing part, supported controller and replace/merge mode.",
        )
    part = selected[0]
    v4 = fields is V4
    node_tag, tick_tag, value_tag = (
        ("cc", "t", "v") if v4 else ("mCtrl", "posTick", "attr")
    )
    identity = CONTROLLERS[controller] if v4 else controller
    values = {}
    old = [
        node
        for node in local(part, node_tag)
        if (
            node.find(f"{{*}}{value_tag}") is not None
            and node.find(f"{{*}}{value_tag}").get("id") == identity
        )
    ]
    if mode == "merge":
        values.update(
            (int(text(node, tick_tag)), int(text(node, value_tag))) for node in old
        )
    low, high = (
        (-8192, 8191)
        if controller == "PIT"
        else (0, 24)
        if controller == "PBS"
        else (0, 127)
    )
    for point in points:
        tick, value = point.get("tick"), point.get("value")
        if (
            type(tick) is not int
            or type(value) is not int
            or tick < 0
            or not low <= value <= high
        ):
            raise IntegrationError(
                "INVALID_CURVE",
                f"{controller} requires nonnegative ticks and values in {low}..{high}.",
            )
        values[tick] = value
    for node in old:
        part.remove(node)
    ns = ET.QName(part).namespace
    insertion = next(
        (
            i
            for i, node in enumerate(part)
            if isinstance(node.tag, str)
            and ET.QName(node).localname in ("note", "plane")
        ),
        len(part),
    )
    for tick, value in sorted(values.items()):
        node = ET.Element(f"{{{ns}}}{node_tag}")
        ET.SubElement(node, f"{{{ns}}}{tick_tag}").text = str(tick)
        ET.SubElement(node, f"{{{ns}}}{value_tag}", id=identity).text = str(value)
        part.insert(insertion, node)
        insertion += 1
    controls = sorted(local(part, node_tag), key=lambda n: int(text(n, tick_tag)))
    for node in controls:
        part.remove(node)
    insertion = next(
        (
            i
            for i, node in enumerate(part)
            if isinstance(node.tag, str)
            and ET.QName(node).localname in ("note", "plane")
        ),
        len(part),
    )
    for node in controls:
        part.insert(insertion, node)
        insertion += 1
    result = dump(tree)
    inspect(result)
    return result


def read_curve(data: bytes, track_index: int, part_index: int, controller: str):
    tree, fields = load(data)
    selected = [
        p for ti, pi, _, p in parts(tree) if (ti, pi) == (track_index, part_index)
    ]
    if len(selected) != 1 or controller not in CONTROLLERS:
        raise IntegrationError(
            "INVALID_CURVE", "Choose an existing part and supported controller."
        )
    v4 = fields is V4
    node_tag, tick_tag, value_tag = (
        ("cc", "t", "v") if v4 else ("mCtrl", "posTick", "attr")
    )
    identity = CONTROLLERS[controller] if v4 else controller
    return [
        {"tick": int(text(node, tick_tag)), "value": int(text(node, value_tag))}
        for node in local(selected[0], node_tag)
        if node.find(f"{{*}}{value_tag}") is not None
        and node.find(f"{{*}}{value_tag}").get("id") == identity
    ]


def set_singer(data: bytes, track_index: int, part_index: int, bank: int, program: int):
    tree, fields = load(data)
    voices = inspect(data)["voices"]
    if not any(v["bank"] == bank and v["program"] == program for v in voices):
        raise IntegrationError(
            "VOICE_NOT_FOUND",
            "Choose an existing voice bank/program from the voice table.",
        )
    selected = [
        p for ti, pi, _, p in parts(tree) if (ti, pi) == (track_index, part_index)
    ]
    if len(selected) != 1:
        raise IntegrationError("INVALID_PART", "Choose an existing part.")
    singers = local(selected[0], "singer")
    if not singers:
        raise IntegrationError(
            "SINGER_EVENT_REQUIRED", "The part has no singer event to modify."
        )
    for singer in singers:
        singer.find("{*}bs" if fields is V4 else "{*}vBS").text = str(bank)
        singer.find("{*}pc" if fields is V4 else "{*}vPC").text = str(program)
    return dump(tree)


def add_notes(
    data: bytes,
    track_index: int,
    part_index: int,
    entries: list[dict],
    *,
    replace_existing: bool = False,
) -> bytes:
    tree, fields = load(data)
    selected = [
        p for ti, pi, _, p in parts(tree) if (ti, pi) == (track_index, part_index)
    ]
    if len(selected) != 1 or not entries:
        raise IntegrationError(
            "INVALID_PART", "Choose an existing part and at least one new note."
        )
    part = selected[0]
    existing = local(part, "note")
    if not existing:
        raise IntegrationError(
            "NOTE_STYLE_REQUIRED",
            "This part has no note style to inherit. Use create_vsqx for a new score.",
        )
    for entry in entries:
        required = set(fields)
        if set(entry) != required:
            raise IntegrationError(
                "INVALID_NEW_NOTE", f"New notes require exactly {sorted(required)}."
            )
        validate_change({"id": "new"} | entry)
        note = deepcopy(existing[0])
        for key, tag in fields.items():
            node = note.find(f"{{*}}{tag}")
            if node is None:
                raise IntegrationError("MISSING_FIELD", key)
            node.text = (
                ET.CDATA(entry[key]) if key in ("lyric", "phoneme") else str(entry[key])
            )
        plane = part.find("{*}plane")
        part.insert(part.index(plane) if plane is not None else len(part), note)
    if replace_existing:
        for note in existing:
            part.remove(note)
    all_notes = local(part, "note")
    for node in all_notes:
        part.remove(node)
    plane = part.find("{*}plane")
    position = part.index(plane) if plane is not None else len(part)
    for node in sorted(all_notes, key=lambda n: int(text(n, fields["start_tick"]))):
        part.insert(position, node)
        position += 1
    duration = part.find("{*}playTime")
    if duration is not None:
        end = max(
            int(text(n, fields["start_tick"])) + int(text(n, fields["duration_tick"]))
            for n in all_notes
        )
        duration.text = str(max(int(duration.text or 0), end))
    return dump(tree)


def delete_notes(data: bytes, note_ids: list[str]) -> bytes:
    tree, fields = load(data)
    lookup = {row["id"]: element for row, element in notes(tree, fields)}
    if (
        not note_ids
        or len(set(note_ids)) != len(note_ids)
        or any(key not in lookup for key in note_ids)
    ):
        raise IntegrationError("INVALID_NOTE_ID", "Supply unique existing note IDs.")
    for key in note_ids:
        note = lookup[key]
        note.getparent().remove(note)
    return dump(tree)


def create(
    voice_source: bytes,
    entries: list[dict],
    bank: int,
    program: int,
    tempo_bpm: float = 120.0,
    track_name: str = "Vocal",
) -> bytes:
    if not 10 <= tempo_bpm <= 400 or not entries:
        raise IntegrationError(
            "INVALID_SCORE", "Provide notes and a tempo from 10 to 400 BPM."
        )
    voices, _ = load(voice_source)
    table = voices.getroot().find("{*}vVoiceTable")
    if table is None or ET.QName(voices.getroot()).localname != "vsq4":
        raise IntegrationError(
            "VOICE_SOURCE_REQUIRED",
            "Use a VSQX4 exported from the target Piapro installation as the voice source.",
        )
    if not any(
        int(text(v, "bs", "0")) == bank and int(text(v, "pc", "0")) == program
        for v in local(table, "vVoice")
    ):
        raise IntegrationError(
            "VOICE_NOT_FOUND",
            "Choose a bank/program present in inspect_project voices.",
        )
    template = Path(__file__).parent / "assets" / "blank.vsqx"
    tree, fields = load(template.read_bytes())
    root = tree.getroot()
    old = root.find("{*}vVoiceTable")
    root.replace(old, deepcopy(table))
    root.find("{*}masterTrack/{*}seqName").text = ET.CDATA(track_name)
    root.find("{*}masterTrack/{*}tempo/{*}v").text = str(round(tempo_bpm * 100))
    _, _, track, part = next(parts(tree))
    track.find("{*}name").text = ET.CDATA(track_name)
    part.find("{*}name").text = ET.CDATA(track_name)
    part.find("{*}singer/{*}bs").text = str(bank)
    part.find("{*}singer/{*}pc").text = str(program)
    first = local(part, "note")[0]
    first.find("{*}y").text = ET.CDATA("あ")
    # add_notes supplies schema/style inheritance, then discard its template note.
    return add_notes(dump(tree), 0, 0, entries, replace_existing=True)
