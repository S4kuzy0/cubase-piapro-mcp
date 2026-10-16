import pytest

from cubase_piapro_mcp import windows
from cubase_piapro_mcp.errors import IntegrationError


def test_editor_identity_excludes_the_cubase_plugin_wrapper(monkeypatch):
    native = {"handle": 1, "visible": True, "class": "Afx:native", "title": "Piapro Studio"}
    wrapper = {"handle": 2, "visible": True, "class": "SteinbergWindowClass", "title": "01 - Piapro Studio VSTi"}
    monkeypatch.setattr(windows, "windows", lambda: [native, wrapper])
    assert windows.piapro_root() == native
    monkeypatch.setattr(windows, "windows", lambda: [wrapper])
    with pytest.raises(IntegrationError, match="PIAPRO_NOT_UNIQUE"):
        windows.piapro_root()
    monkeypatch.setattr(windows, "windows", lambda: [native, native | {"handle": 3}])
    with pytest.raises(IntegrationError, match="PIAPRO_NOT_UNIQUE"):
        windows.piapro_root()
