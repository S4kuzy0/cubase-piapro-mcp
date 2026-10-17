from cubase_piapro_mcp import ppsf


def test_enabled_evec_contains_a_length_prefixed_voicebank_name():
    raw = bytes.fromhex('022d3165000000164d494b555f5634585f4f726967696e616c5f4556454303435656')
    entry = ppsf.model.PpsfVocaloidEVECData.parse(raw)
    assert entry.voicebank_name == 'MIKU_V4X_Original_EVEC'
    assert entry.unknown_int == 101 and entry.type_name == 'CVV'
    assert ppsf.model.PpsfVocaloidEVECData.build(entry) == raw


def test_native_vibrato_point_uses_16_bit_value_and_32_bit_position():
    raw = bytes.fromhex('400083620600')
    point = ppsf.model.PpsfBPValue.parse(raw)
    assert point.value == 64
    assert point.position == 418435
    assert ppsf.model.PpsfBPValue.build(point) == raw
