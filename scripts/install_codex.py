"""Register the verified stdio launcher, preserving the user's current config."""

from __future__ import annotations

import argparse
import codecs
from copy import deepcopy
import hashlib
import json
import os
import re
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def set_tool_timeout(config, backup):
    """Preserve every unrelated TOML value while extending this server's calls."""
    before = config.read_bytes()
    text = before.decode('utf-8-sig')
    original = tomllib.loads(text)
    expected = deepcopy(original)
    expected['mcp_servers']['cubase-piapro']['tool_timeout_sec'] = 900
    if original == expected:
        return
    match = re.search(r'(?m)^\[mcp_servers\.cubase-piapro\]\r?$', text)
    if match is None:
        raise RuntimeError('No unique CLI-generated MCP table for the timeout setting')
    end = re.search(r'(?m)^\[', text[match.end():])
    stop = match.end() + end.start() if end else len(text)
    body = text[match.end():stop]
    newline = '\r\n' if '\r\n' in text else '\n'
    if re.search(r'(?m)^tool_timeout_sec\s*=', body):
        body, count = re.subn(r'(?m)^tool_timeout_sec\s*=.*?\r?$', 'tool_timeout_sec = 900', body)
        if count != 1:
            raise RuntimeError('Ambiguous MCP timeout field')
    else:
        body = newline + 'tool_timeout_sec = 900' + body
    changed = text[:match.end()] + body + text[stop:]
    if tomllib.loads(changed) != expected:
        raise RuntimeError('Timeout update would alter unrelated Codex settings')
    backup.write_bytes(before)
    if backup.read_bytes() != before or config.read_bytes() != before:
        raise RuntimeError('Config backup failed or concurrent config change detected')
    config.write_bytes((codecs.BOM_UTF8 if before.startswith(codecs.BOM_UTF8) else b'') + changed.encode('utf8'))
    if tomllib.loads(config.read_text(encoding='utf-8-sig')) != expected:
        raise RuntimeError('MCP timeout readback failed; restore the retained config backup')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--codex", default="codex")
    parser.add_argument(
        "--focus-click",
        action="store_true",
        help="Retain the legacy focus-click setting; current native file operations do not use it",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runtime = Path(args.python).resolve(strict=True)
    state = Path(args.state_dir).resolve()
    state.mkdir(parents=True, exist_ok=True)
    config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config = config_home / "config.toml"
    before = config.read_bytes() if config.exists() else None
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    backup = state / ("codex-config-before-" + stamp + ".toml")
    if before is not None:
        backup.write_bytes(before)
        if backup.read_bytes() != before:
            raise RuntimeError("Config backup verification failed")
    command = [
        args.codex,
        "mcp",
        "add",
        "cubase-piapro",
        "--env",
        f"CUBASE_PIAPRO_STATE_DIR={state}",
        "--env",
        "PYTHONUTF8=1",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "CUBASE_PIAPRO_FOCUS_CLICK=" + ("1" if args.focus_click else "0"),
        "--",
        str(runtime),
        str(root / "run_server.py"),
    ]
    subprocess.run(command, check=True)
    timeout_backup = state / ('codex-config-before-timeout-' + stamp + '.toml')
    set_tool_timeout(config, timeout_backup)
    result = subprocess.run(
        [args.codex, "mcp", "get", "cubase-piapro", "--json"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    entry = json.loads(result.stdout)
    if not entry.get("enabled"):
        raise RuntimeError("The new MCP entry is not enabled")
    report = {
        "name": "cubase-piapro",
        "enabled": True,
        "tool_timeout_sec": 900,
        "timeout_config_backup": str(timeout_backup) if timeout_backup.exists() else None,
        "focus_click_enabled": args.focus_click,
        "config_path": str(config),
        "config_backup": str(backup) if before is not None else None,
        "config_backup_sha256": hashlib.sha256(before).hexdigest()
        if before is not None
        else None,
        "python": str(runtime),
        "launcher": str(root / "run_server.py"),
        "entry": entry,
    }
    (state / "codex-install.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "entry"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
