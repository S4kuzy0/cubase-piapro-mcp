import importlib.util
from pathlib import Path
import tomllib

import pytest

spec = importlib.util.spec_from_file_location('install_codex', Path(__file__).resolve().parents[1] / 'scripts/install_codex.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.mark.parametrize('old_timeout,newline', [('', '\n'), ('tool_timeout_sec = 60\n', '\r\n')])
def test_installer_timeout_preserves_every_other_setting_and_verified_backup(tmp_path, old_timeout, newline):
    text = ('model = "example"\n[mcp_servers.other]\ncommand = "other"\ntool_timeout_sec = 12\n'
            '[mcp_servers.cubase-piapro]\n' + old_timeout + 'command = "python"\nargs = ["run_server.py"]\n'
            '[mcp_servers.cubase-piapro.env]\nPYTHONUTF8 = "1"\n[projects.example]\ntrust_level = "trusted"\n').replace('\n', newline)
    config = tmp_path / 'config.toml'; config.write_bytes(text.encode())
    backup = tmp_path / 'before.toml'
    expected = tomllib.loads(text); expected['mcp_servers']['cubase-piapro']['tool_timeout_sec'] = 900
    installer.set_tool_timeout(config, backup)
    assert backup.read_bytes() == text.encode()
    assert tomllib.loads(config.read_text()) == expected


def test_installer_timeout_already_set_is_an_exact_noop(tmp_path):
    original = b'[mcp_servers.cubase-piapro]\ncommand = "python"\ntool_timeout_sec = 900\n'
    config = tmp_path / 'config.toml'; config.write_bytes(original)
    backup = tmp_path / 'unused.toml'
    installer.set_tool_timeout(config, backup)
    assert config.read_bytes() == original and not backup.exists()


def test_installer_unknown_table_format_refuses_before_config_write(tmp_path):
    original = b'[mcp_servers."cubase-piapro"]\ncommand = "python"\n'
    config = tmp_path / 'config.toml'; config.write_bytes(original)
    with pytest.raises(RuntimeError, match='No unique'):
        installer.set_tool_timeout(config, tmp_path / 'before.toml')
    assert config.read_bytes() == original
