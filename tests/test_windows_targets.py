import pytest

from cubase_piapro_mcp import windows
from cubase_piapro_mcp.errors import IntegrationError
from types import SimpleNamespace


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


@pytest.mark.parametrize("child", [False, True])
def test_transient_windows_may_disappear_during_enumeration(monkeypatch, child):
    class WindowError(Exception):
        winerror = 1400
    def record(hwnd):
        if hwnd == 1:
            raise WindowError("window closed between enumeration and GetClassName")
        return {"handle": hwnd, "pid": 42}
    def enumerate_windows(visit, value):
        visit(1, value)
        visit(2, value)
    gui = SimpleNamespace(EnumWindows=enumerate_windows,
                          EnumChildWindows=lambda root, visit, value: enumerate_windows(visit, value))
    api = SimpleNamespace(OpenProcess=lambda *args: SimpleNamespace(Close=lambda: None))
    process = SimpleNamespace(GetModuleFileNameEx=lambda *args: "C:/Cubase14.exe")
    monkeypatch.setattr(windows, "api", lambda: (api, gui, process))
    monkeypatch.setattr(windows, "window_record", record)
    result = windows.children(123) if child else windows.windows()
    assert [item["handle"] for item in result] == [2]


@pytest.mark.parametrize("child", [False, True])
def test_window_enumeration_does_not_mask_unrelated_errors(monkeypatch, child):
    class WindowError(Exception):
        winerror = 5
    def record(hwnd):
        raise WindowError("unrelated API failure")
    gui = SimpleNamespace(EnumWindows=lambda visit, value: visit(1, value),
                          EnumChildWindows=lambda root, visit, value: visit(1, value))
    monkeypatch.setattr(windows, "api", lambda: (None, gui, None))
    monkeypatch.setattr(windows, "window_record", record)
    with pytest.raises(WindowError):
        windows.children(123) if child else windows.windows()
