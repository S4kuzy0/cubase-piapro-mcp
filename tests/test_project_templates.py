from pathlib import Path

import pytest

from cubase_piapro_mcp import project_templates
from cubase_piapro_mcp.errors import IntegrationError
from cubase_piapro_mcp.storage import digest


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))
    source = tmp_path / "original.cpr"
    source.write_bytes(b"RIFF\x00\x00\x00\x00NUNDopaque-template-test-data")
    project_templates.register(str(source), digest(source.read_bytes()), "piapro-empty",
                               "Test only", standalone_no_external_media=True)
    return tmp_path, source


def test_copy_preserves_native_bytes_and_refuses_existing_project(installed):
    root, source = installed
    target = root / "new-song"
    created = project_templates.create_copy("piapro-empty", str(target))
    assert Path(created["path"]).read_bytes() == source.read_bytes()
    assert not created["live_state_verified"]
    user_file = target / "keep.txt"
    user_file.write_text("user content")
    with pytest.raises(IntegrationError, match="PROJECT_DIRECTORY_EXISTS"):
        project_templates.create_copy("piapro-empty", str(target))
    assert user_file.read_text() == "user content"


def test_changed_template_and_directory_traversal_are_rejected_before_creation(installed):
    root, _ = installed
    _, template, _ = project_templates.load("piapro-empty")
    template.write_bytes(b"changed")
    with pytest.raises(IntegrationError, match="TEMPLATE_UNAVAILABLE"):
        project_templates.create_copy("piapro-empty", str(root / "must-not-exist"))
    assert not (root / "must-not-exist").exists()
    with pytest.raises(IntegrationError, match="INVALID_TEMPLATE_ID"):
        project_templates.load("../escape")


def test_external_media_assumption_requires_operator_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))
    source = tmp_path / "media.cpr"
    source.write_bytes(b"opaque CPR")
    with pytest.raises(IntegrationError, match="TEMPLATE_MEDIA_UNSUPPORTED"):
        project_templates.register(str(source), digest(source.read_bytes()), "unsafe", "",
                                   standalone_no_external_media=False)
