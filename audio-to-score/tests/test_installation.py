import importlib.util
from pathlib import Path
import tomllib

import pytest


def installer():
    source = Path(__file__).parents[1] / "scripts/install_codex.py"
    spec = importlib.util.spec_from_file_location("audio_installer", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registration_preserves_unrelated_toml_and_exact_backup(tmp_path):
    before = b'\xef\xbb\xbfmodel = "unchanged"\r\n[mcp_servers.cubase-piapro]\r\ncommand = "stable"\r\nargs = ["-I", "-m", "original"]\r\n'
    path = tmp_path / "config.toml"
    path.write_bytes(before)
    install = installer()
    result = install.register(path, tmp_path / "python.exe", tmp_path / "audio.json", tmp_path / "backups")
    assert Path(result["backup"]).read_bytes() == before
    original = tomllib.loads(before.decode("utf-8-sig"))
    changed = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    entry = changed["mcp_servers"].pop("vocal-to-score")
    assert changed == original
    assert entry["args"] == ["-I", "-B", "-X", "utf8", "-m", "vocal_to_score_mcp.server"]
    first = path.read_bytes()
    assert install.register(path, tmp_path / "python.exe", tmp_path / "audio.json", tmp_path / "backups") == {"changed": False}
    assert path.read_bytes() == first


def test_existing_conflicting_server_is_preserved(tmp_path):
    path = tmp_path / "config.toml"
    before = b'[mcp_servers.vocal-to-score]\ncommand = "user-configured"\n'
    path.write_bytes(before)
    with pytest.raises(ValueError, match="differs"):
        installer().register(path, tmp_path / "python", tmp_path / "audio.json", tmp_path / "backups")
    assert path.read_bytes() == before


def test_accepted_version_does_not_authorize_a_different_runtime():
    import hashlib
    install = installer()
    runtime = {"version": "0.1.0", "runtime_files_sha256": {"server.py": "accepted"}}
    acceptance = {"offline_verified": True, "piapro_copy_verified": True, "package_version": "0.1.0",
                  "config_sha256": hashlib.sha256(b"config").hexdigest(),
                  "runtime_files_sha256": {"server.py": "accepted"}}
    install.validate_acceptance(runtime, acceptance, b"config")
    changed = {**runtime, "runtime_files_sha256": {"server.py": "unaccepted source"}}
    with pytest.raises(ValueError, match="exact installed"):
        install.validate_acceptance(changed, acceptance, b"config")
    with pytest.raises(ValueError):
        install.validate_acceptance(runtime, acceptance, b"different settings")


def test_skill_existing_content_has_readable_recovery(tmp_path):
    import tarfile
    source, target, backups = [tmp_path / name for name in ("source", "target", "backups")]
    for path in (source, target, backups):
        path.mkdir()
    (source / "SKILL.md").write_text("new")
    (target / "SKILL.md").write_text("user's old skill")
    (target / "personal.txt").write_text("keep")
    installer().install_skill(source, target, backups)
    assert (target / "SKILL.md").read_text() == "new"
    assert (target / "personal.txt").read_text() == "keep"
    with tarfile.open(next(backups.glob("*.tar"))) as archive:
        assert archive.extractfile("target/SKILL.md").read() == b"user's old skill"
