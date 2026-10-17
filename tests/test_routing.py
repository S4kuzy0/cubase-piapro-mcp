import struct

import pytest

from cubase_piapro_mcp import routing, native_visual, windows
from cubase_piapro_mcp.errors import IntegrationError


def test_instrument_inspector_known_all_inputs_ocr_omission():
    assert routing.key("All MIDI Inputs") in routing.key("司 AII MIDI 《 nputs")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(routing.mido, "get_input_names", lambda: ["CP MCP To Cubase 0", "CP MCP From Cubase 1", "Keyboard 2"])
    monkeypatch.setattr(routing.mido, "get_output_names", lambda: ["CP MCP To Cubase 1", "CP MCP From Cubase 2"])
    monkeypatch.setattr(routing.live_midi, "selected_ports", lambda: ("CP MCP From Cubase 1", "CP MCP To Cubase 1"))


def test_control_ports_are_never_music_targets_and_labels_use_live_owners():
    snapshot = {"tracks": [{"kind": "instrument", "plugin": "Piapro Studio VSTi", "uid": "owner"}]}
    assert not routing.canonical_route("CP MCP To Cubase", "output", snapshot)["selectable"]
    assert not routing.canonical_route("CP MCP From Cubase", "input", snapshot)["selectable"]
    assert not routing.canonical_route("AI 1 M 旧 1 lnputs", "input", snapshot)["selectable"]
    assert routing.canonical_route("Key board", "input", snapshot)["label"] == "Keyboard"
    assert routing.canonical_route("02. Piapro Studio VSTi - MIDI 《 n", "output", snapshot)["instrument_uid"] == "owner"
    assert routing.canonical_route("01 ． Piapro Studio VSTi - M 旧 ] 《 n", "output", snapshot)["instrument_uid"] == "owner"
    assert routing.canonical_route("Ch 0 rd Pads", "input", snapshot)["label"] == "Chord Pads"
    assert routing.canonical_route("02. Unrecognized Synth", "output", snapshot) is None
    snapshot["tracks"].append(snapshot["tracks"][0] | {"uid": "duplicate-owner"})
    assert routing.canonical_route("02. Piapro Studio VSTi - MIDI In", "output", snapshot) is None


def test_popup_cancel_goes_to_the_popup_and_never_its_search_edit(monkeypatch):
    class GUI:
        visible = True
        calls = []
        def IsWindow(self, hwnd): return True
        def IsWindowVisible(self, hwnd): return self.visible
        def PostMessage(self, hwnd, message, value, flags):
            self.calls.append((hwnd, message, value, flags))
            if hwnd == 1 and value == 0x1B:
                self.visible = False
    gui = GUI()
    monkeypatch.setattr(routing.windows, "api", lambda: (None, gui, None))
    routing._close({"handle": 1}, {"handle": 2})
    assert [call[0] for call in gui.calls] == [1, 1]
    assert gui.calls[-1][-1] == 0xC0000001


def test_instrument_output_is_rejected_before_any_selector(monkeypatch):
    monkeypatch.setattr(routing, "_context", lambda *args: {"track": {"kind": "instrument"}})
    monkeypatch.setattr(routing, "_read", lambda ctx: pytest.fail("An instrument slot must not be opened as MIDI output"))
    with pytest.raises(IntegrationError, match="MIDI_OUTPUT_NOT_APPLICABLE"):
        routing.set_routing("id", "revision", "routing", output_route_id="route", allow_dialog_fallback=True)


def test_stale_routing_never_reaches_a_route_choice(monkeypatch):
    monkeypatch.setattr(routing, "_context", lambda *args: {"track": {"kind": "midi"}})
    monkeypatch.setattr(routing, "_read", lambda ctx: {"revision": "fresh"})
    monkeypatch.setattr(routing, "_choose", lambda *args: pytest.fail("A stale routing request must not commit"))
    with pytest.raises(IntegrationError, match="ROUTING_CHANGED"):
        routing.set_routing("id", "host", "stale", input_route_id="route", allow_dialog_fallback=True)


def test_fallback_must_be_explicit_before_any_host_access(monkeypatch):
    monkeypatch.setattr(routing.cubase, "project_root", lambda: pytest.fail("No host access without fallback opt-in"))
    with pytest.raises(IntegrationError, match="DIALOG_FALLBACK_REQUIRED"):
        routing.get_routing("id", "host")


@pytest.mark.parametrize("unrelated_output_changed", [False, True])
def test_failure_recovery_checks_both_routes(monkeypatch, tmp_path, unrelated_output_changed):
    def direction(selected):
        return {"selected": {"id": selected}, "options": [
            {"id": "old-input", "selectable": True}, {"id": "new-input", "selectable": True}]}
    before = {"revision": "fresh", "input": direction("old-input"), "output": direction("old-output")}
    recovered = before | {"output": direction("unexpected-output" if unrelated_output_changed else "old-output")}
    ctx = {"track": {"kind": "midi"}, "folder": tmp_path}
    monkeypatch.setattr(routing, "_context", lambda *args: ctx)
    reads = iter([before, IntegrationError("READBACK_FAILED", "Test failure after writing"), recovered])
    def read(context):
        result = next(reads)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(routing, "_read", read)
    choices = []
    monkeypatch.setattr(routing, "_choose", lambda ctx, direction, route, **kwargs: choices.append((direction, route)))
    expected = not unrelated_output_changed
    with pytest.raises(IntegrationError, match=f"automatic route restoration verified: {expected}"):
        routing.set_routing("id", "host", "fresh", input_route_id="new-input", allow_dialog_fallback=True)
    assert choices == [("input", "new-input"), ("input", "old-input")]
    assert (tmp_path / "before.json").is_file()


def bitmap(path, arrows):
    width, height = 128, 96
    data = bytearray(width * height * 3)
    for ox, oy in arrows:
        for y, count in enumerate([16, 16, 12, 12, 12, 12, 8, 8, 8, 8, 4, 4, 4, 4]):
            for x in range(ox + (16 - count) // 2, ox + (16 + count) // 2):
                offset = ((height - 1 - oy - y) * width + x) * 3
                data[offset:offset + 3] = b"\xff\xff\xff"
    header = b"BM" + struct.pack("<IHHI", 54 + len(data), 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(data), 0, 0, 0, 0)
    path.write_bytes(header + dib + data)
    return native_visual.Bitmap(path)


def test_down_arrow_requires_a_unique_actual_triangle(tmp_path):
    image = bitmap(tmp_path / "single.bmp", [(20, 20)])
    assert image.down_arrow(0, 0, 128, 96, 3) == (27.5, 26.5)
    image = bitmap(tmp_path / "ambiguous.bmp", [(20, 20), (70, 20)])
    with pytest.raises(IntegrationError, match="UNSUPPORTED_ROUTING_LAYOUT"):
        image.down_arrow(0, 0, 128, 96, 3)


def test_background_activation_uses_no_foreground_or_mouse_input(monkeypatch):
    class GUI:
        calls = []
        def IsWindowEnabled(self, hwnd): return True
        def SendMessageTimeout(self, *args): self.calls.append(args)
    gui = GUI()
    monkeypatch.setattr(windows, "api", lambda: (None, gui, None))
    monkeypatch.setattr(windows, "windows", lambda: [{"handle": 123, "visible": True}])
    monkeypatch.setattr(windows, "activate_window", lambda hwnd: pytest.fail("Foreground input is not needed"))
    assert windows.notify_activation(123) == "native_activation_notice"
    assert gui.calls == [(123, 0x6, 1, 0, 2, 1000)]
