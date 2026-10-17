"""Register the verified stdio launcher, preserving the user's current config."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


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
