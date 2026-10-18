"""Register only an accepted, installed audio package and its companion skill."""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tomllib

FLAGS = ["-I", "-B", "-X", "utf8"]
MODULE = "vocal_to_score_mcp.server"


def installed_runtime(python: Path):
    script = ("import json,sys,hashlib,vocal_to_score_mcp as p;from pathlib import Path;"
              "assert Path(p.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),'Source checkout is not an installed release';"
              "root=Path(p.__file__).parent;"
              "files={str(f.relative_to(root)).replace(chr(92),'/'):hashlib.sha256(f.read_bytes()).hexdigest() for f in root.rglob('*.py')};"
              "print(json.dumps({'version':p.__version__,'file':p.__file__,'runtime_files_sha256':files}))")
    result = subprocess.run([str(python), *FLAGS, "-c", script], check=True, capture_output=True, text=True, encoding="utf8")
    return json.loads(result.stdout)


def validate_acceptance(runtime, acceptance, config_bytes):
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    if (acceptance.get("offline_verified") is not True or acceptance.get("piapro_copy_verified") is not True
            or acceptance.get("config_sha256") != config_hash or acceptance.get("package_version") != runtime["version"]
            or not runtime.get("runtime_files_sha256")
            or acceptance.get("runtime_files_sha256") != runtime["runtime_files_sha256"]):
        raise ValueError("Complete offline and E-copy Piapro acceptance for this exact installed package and configuration before registration")


def register(config: Path, python: Path, audio_config: Path, backups: Path):
    before = config.read_bytes() if config.exists() else b""
    original = tomllib.loads(before.decode("utf-8-sig"))
    entry = {"command": str(python), "args": [*FLAGS, "-m", MODULE], "enabled": True,
             "tool_timeout_sec": 120, "env": {"VOCAL_TO_SCORE_CONFIG": str(audio_config)}}
    existing = original.get("mcp_servers", {}).get("vocal-to-score")
    if existing == entry:
        return {"changed": False}
    if existing is not None:
        raise ValueError("Existing vocal-to-score entry differs; preserve and review it before updating")
    expected = deepcopy(original)
    expected.setdefault("mcp_servers", {})["vocal-to-score"] = entry
    newline = "\r\n" if b"\r\n" in before else "\n"
    fields = ["", "[mcp_servers.vocal-to-score]", "command = " + json.dumps(str(python)),
              "args = " + json.dumps(entry["args"]), "enabled = true", "tool_timeout_sec = 120",
              "[mcp_servers.vocal-to-score.env]", "VOCAL_TO_SCORE_CONFIG = " + json.dumps(str(audio_config)), ""]
    changed = before + newline.join(fields).encode("utf8")
    if tomllib.loads(changed.decode("utf-8-sig")) != expected:
        raise ValueError("Registration would change unrelated settings")
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / ("codex-before-audio-" + stamp + ".toml")
    with backup.open("xb") as stream:
        stream.write(before)
    if backup.read_bytes() != before or (config.read_bytes() if config.exists() else b"") != before:
        raise ValueError("Backup verification or concurrent configuration check failed")
    config.write_bytes(changed)
    if tomllib.loads(config.read_text(encoding="utf-8-sig")) != expected:
        raise ValueError("Registration readback failed; retained backup identifies the exact prior state")
    return {"changed": True, "backup": str(backup), "backup_sha256": hashlib.sha256(before).hexdigest()}


def install_skill(source: Path, destination: Path, backups: Path):
    backups.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        backup = backups / ("skill-before-audio-" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f") + ".tar")
        with tarfile.open(backup, "x") as bundle:
            bundle.add(destination, arcname=destination.name)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    for path in source.rglob("*"):
        if path.is_file() and path.read_bytes() != (destination / path.relative_to(source)).read_bytes():
            raise ValueError("Skill copy readback failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--audio-config", required=True, type=Path)
    parser.add_argument("--acceptance", required=True, type=Path)
    parser.add_argument("--skill-source", required=True, type=Path)
    args = parser.parse_args()
    runtime = installed_runtime(args.python.resolve(strict=True))
    acceptance = json.loads(args.acceptance.read_text(encoding="utf8"))
    audio_config = args.audio_config.resolve(strict=True)
    validate_acceptance(runtime, acceptance, audio_config.read_bytes())
    local = json.loads(audio_config.read_text(encoding="utf-8-sig"))
    root = Path(local["root"]).resolve(strict=True)
    if root.drive.lower() != "e:":
        raise ValueError("Use an E-drive runtime")
    codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve(strict=True)
    backups = root / "backups"
    result = register(codex_dir / "config.toml", args.python.resolve(), audio_config, backups)
    install_skill(args.skill_source.resolve(strict=True), codex_dir / "skills/piapro-cover", backups)
    print(json.dumps({"runtime": runtime, "registration": result, "skill_installed": True}))
