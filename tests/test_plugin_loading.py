import copy
import ctypes
from types import SimpleNamespace

import pytest

from cubase_piapro_mcp import plugin_ui, plugin_catalog, plugin_loading, host_controls as hc
from cubase_piapro_mcp.errors import IntegrationError


def test_actual_channel_role_ocr_with_icon_and_empty_cell():
    recognized = {"rows": [{"text": "Channel", "box": [86, 422, 220, 450]},
                            {"text": "lnser.", "box": [82, 826, 166, 852]},
                            {"text": "Sends 璺", "box": [80, 1020, 232, 1052]}]}
    bitmap = SimpleNamespace(pixel=lambda *args: (34, 38, 40))
    assert plugin_ui.insert_anchor(recognized, bitmap, 0, 1.5, empty=True) == (116, 458.5)
    with pytest.raises(IntegrationError, match="UNSUPPORTED_PLUGIN_LAYOUT"):
        plugin_ui.insert_anchor(recognized, bitmap, 2, 1.5, empty=True)
    with pytest.raises(IntegrationError, match="PLUGIN_SLOT_NOT_VISIBLY_EMPTY"):
        plugin_ui.insert_anchor(recognized, SimpleNamespace(pixel=lambda *args: (30, 120, 130)), 0, 1.5, empty=True)


def test_occupied_header_ocr_keeps_static_words_separate_from_icon_glyphs():
    recognized = {"rows": [{"text": "Channel", "box": [86, 422, 220, 450]},
                            {"text": "lnser. 。", "box": [82, 826, 222, 852]},
                            {"text": "RoomW..SE", "box": [80, 910, 220, 930]},
                            {"text": "Sends 里", "box": [80, 1086, 232, 1118]}]}
    bitmap = SimpleNamespace(pixel=lambda *args: (34, 38, 40))
    assert plugin_ui.insert_anchor(recognized, bitmap, 0, 1.5, empty=False) == (116, 458.5)


def test_search_uses_timed_wm_gettext_instead_of_cross_process_caption(monkeypatch):
    def make_signature(*args):
        def bind(function):
            assert function[0] == "SendMessageTimeoutW"
            def send(hwnd, message, capacity, address, flags, timeout, result):
                assert message == 0xD and capacity == 256 and timeout == 1500
                text = ctypes.create_unicode_buffer("Mutant VSTi ")
                ctypes.memmove(address, ctypes.addressof(text), ctypes.sizeof(text))
                result._obj.value = len(text.value)
                return 1
            return send
        return bind
    gui = SimpleNamespace(GetClassName=lambda h: "Edit",
        GetWindowText=lambda h: pytest.fail("Cross-process captions are empty for this real native Edit"))
    monkeypatch.setattr(plugin_ui.windows, "api", lambda: (None, gui, None))
    monkeypatch.setattr(plugin_ui.ctypes, "WINFUNCTYPE", make_signature)
    assert plugin_ui.native_edit_text(12) == "Mutant VSTi "


def test_ambiguous_cached_names_and_stale_catalog_are_refused():
    catalog = {"revision": "current", "plugins": [{"id": "a", "selector_ambiguous": True}]}
    with pytest.raises(IntegrationError, match="PLUGIN_CATALOG_CHANGED"):
        plugin_catalog.requested_plugin(catalog, "a", "old")
    with pytest.raises(IntegrationError, match="PLUGIN_ID_UNAVAILABLE"):
        plugin_catalog.requested_plugin(catalog, "a", "current")


def test_local_scan_cache_preserves_ids_and_ignores_nonstandard_path_entities(tmp_path):
    uid = "A" * 32
    (tmp_path / "VstPlugInfoV4.xml").write_text('<VstPlugInfo><obj class="VstPlugInfo"><string name="uid" value="' + uid + '"/><string name="name" value="Test Synth"/></obj></VstPlugInfo>', encoding="utf-8")
    (tmp_path / "Vst2xPlugin Infos Test.xml").write_text('''<Vst2xPlugins><item><string name="Group" value="E:/&sup3;/plugin.dll"/><member name="Values"><member name="Vst2xPlugInfo">
        <string name="cid" value="''' + uid + '''"/><string name="subCategory" value="Instrument"/>
        <string name="name" value="Test Synth"/><string name="vendor" value="Test Vendor"/>
        <string name="vendorVersion" value="1.0"/><string name="sdkVersion" value="VST 2.4"/>
        </member></member></item></Vst2xPlugins>''', encoding="utf-8")
    result = plugin_catalog.read_catalog(tmp_path)
    assert len(result["plugins"]) == 1
    assert result["plugins"][0]["kind"] == "instrument"
    assert result["plugins"][0]["vendor"] == "Test Vendor"
    assert not result["activation_verified"]
    assert "plugin.dll" not in str(result)


def test_loaded_plugin_comparison_preserves_all_other_groups():
    value = {"plugins": {"insert.0": {"name": ""}, "instrument": {"name": "Voice"}},
             "values": [], "parameter_overflow": False, "revision": "old", "driver_revision": 1}
    before = {group: copy.deepcopy(value) for group in hc.GROUPS}
    after = copy.deepcopy(before)
    for group in hc.GROUPS:
        after[group]["plugins"]["insert.0"]["name"] = "Reverb"
        after[group]["driver_revision"] = 2
    after["insert.0"]["values"] = [{"id": "mix", "value": .5}]
    assert plugin_loading.verify_insert(before, after, "insert.0")
    after["eq"]["values"] = [{"id": "gain", "value": .8}]
    assert not plugin_loading.verify_insert(before, after, "insert.0")


def test_actual_discard_question_ocr_keeps_exact_subject_identity():
    from cubase_piapro_mcp import plugin_ui
    rows = [{"text": 'Discard 訓 changes t0 Piapro Stud io VSTi 01 _ RoomWorks S E"?'},
            {"text": "Discard"}, {"text": "Keep"}]
    assert plugin_ui.discard_question_matches(rows, "Piapro Studio VSTi 01", "RoomWorks SE", "zh-Hans-CN")
    assert not plugin_ui.discard_question_matches(rows, "Piapro Studio VSTi 02", "RoomWorks SE", "zh-Hans-CN")
    assert not plugin_ui.discard_question_matches(rows, "Piapro Studio VSTi 01", "RoomWorks", "zh-Hans-CN")


@pytest.mark.parametrize("text", [
    'Discard all changes to "Other - RoomWorks SE"?',
    'Discard all changes to "Voice - Other"?',
    'Discard all changes to "Voice - RoomWorks SE"? and another object',
    'Discard all changes to "Voice - RoomWorks SE"',
])
def test_discard_rejects_wrong_or_incomplete_questions(text):
    from cubase_piapro_mcp import plugin_ui
    assert not plugin_ui.discard_question_matches([{"text": text}], "Voice", "RoomWorks SE", "en-US")


def test_discard_separator_alias_does_not_change_punctuation_inside_names():
    from cubase_piapro_mcp import plugin_ui
    rows = [{"text": 'Discard all changes to "Voice_A _ RoomWorks SE"?'}]
    assert not plugin_ui.discard_question_matches(rows, "Voice-A", "RoomWorks SE", "zh-Hans-CN")


def test_canonical_discard_question_remains_supported():
    from cubase_piapro_mcp import plugin_ui
    rows = [{"text": 'Discard all changes to "Voice - RoomWorks SE"?'}]
    assert plugin_ui.discard_question_matches(rows, "Voice", "RoomWorks SE", "en-US")


def test_duplicate_discard_questions_are_ambiguous():
    from cubase_piapro_mcp import plugin_ui
    rows = [{"text": 'Discard all changes to "Voice - RoomWorks SE"?'}] * 2
    assert not plugin_ui.discard_question_matches(rows, "Voice", "RoomWorks SE", "en-US")
