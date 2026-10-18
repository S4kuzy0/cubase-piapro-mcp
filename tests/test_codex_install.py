import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import venv

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


def test_registered_launcher_uses_installed_package_despite_workspace_shadow(tmp_path, monkeypatch):
    environment = tmp_path / 'runtime'
    venv.EnvBuilder(with_pip=False).create(environment)
    runtime = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    packages = (environment / 'Lib/site-packages' if os.name == 'nt' else
                environment / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
    installed = packages / 'cubase_piapro_mcp'
    installed.mkdir(parents=True)
    (installed / '__init__.py').write_text('')
    (installed / 'server.py').write_text(
        'if __name__ == "__main__":\n    print("installed stable package")\n'
    )
    workspace = tmp_path / 'unreleased-workspace'
    shadow = workspace / 'cubase_piapro_mcp'
    shadow.mkdir(parents=True)
    (shadow / '__init__.py').write_text('raise RuntimeError("unreleased workspace imported")\n')

    config_home = tmp_path / 'codex-home'
    config_home.mkdir()
    (config_home / 'config.toml').write_text(
        '[mcp_servers.cubase-piapro]\ncommand = "python"\ntool_timeout_sec = 900\n'
    )
    monkeypatch.setenv('CODEX_HOME', str(config_home))
    monkeypatch.setattr(sys, 'argv', ['install_codex.py', '--python', str(runtime),
                                     '--state-dir', str(tmp_path / 'state'), '--codex', 'codex-test-stub'])
    actual_run = subprocess.run
    registration = []

    def run(command, **kwargs):
        if command[0] != 'codex-test-stub':
            return actual_run(command, **kwargs)
        if command[1:3] == ['mcp', 'add']:
            registration.extend(command[command.index('--') + 1:])
            return subprocess.CompletedProcess(command, 0)
        assert command[1:4] == ['mcp', 'get', 'cubase-piapro']
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'enabled': True}))

    monkeypatch.setattr(installer.subprocess, 'run', run)
    installer.main()
    result = actual_run(registration, cwd=workspace,
                        env=dict(os.environ, PYTHONPATH=str(workspace)),
                        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'installed stable package'


def test_registration_missing_installed_package_preserves_existing_config(tmp_path, monkeypatch):
    environment = tmp_path / 'runtime'
    venv.EnvBuilder(with_pip=False).create(environment)
    runtime = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    config_home = tmp_path / 'codex-home'
    config_home.mkdir()
    config = config_home / 'config.toml'
    original = b'[mcp_servers.other]\ncommand = "unchanged"\n'
    config.write_bytes(original)
    state = tmp_path / 'state'
    monkeypatch.setenv('CODEX_HOME', str(config_home))
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1] / 'src'))
    monkeypatch.setattr(sys, 'argv', ['install_codex.py', '--python', str(runtime),
                                     '--state-dir', str(state)])
    with pytest.raises(RuntimeError, match='verified MCP wheel'):
        installer.main()
    assert config.read_bytes() == original
    assert not state.exists()
