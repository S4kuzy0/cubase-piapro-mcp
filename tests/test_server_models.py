from cubase_piapro_mcp.server import HostControlChange, NoteChange


def test_note_changes_keep_existing_lyric_and_phoneme_fields():
    change = NoteChange(id="t0:p0:n0", lyric="あ", phoneme="a")
    assert change.model_dump(exclude_none=True) == {
        "id": "t0:p0:n0", "lyric": "あ", "phoneme": "a"
    }


def test_host_control_model_emits_only_supported_dispatch_fields():
    from cubase_piapro_mcp.host_controls import validate_changes
    from test_host_controls import normalized

    change = HostControlChange(id="eq.2.gain", value=.6).model_dump()
    assert set(change) == {"id", "value"}
    assert validate_changes(normalized(), [change]) == [change]
