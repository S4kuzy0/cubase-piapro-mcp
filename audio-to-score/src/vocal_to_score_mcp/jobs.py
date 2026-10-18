"""Validate requests and start finite, isolated work; never connect to a DAW."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from .storage import AudioError, describe, job_directory, load_config, read_json, sha256, write_json

OPERATIONS = {"separate", "transcribe", "align", "prepare_score"}
AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aiff"}


def now():
    return datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai")).isoformat()


def model_environment(config: dict):
    root = Path(config["root"])
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for key, leaf in {
        "TEMP": "tmp", "TMP": "tmp", "TORCH_HOME": "cache/torch",
        "HF_HOME": "cache/huggingface", "XDG_CACHE_HOME": "cache/xdg",
        "NUMBA_CACHE_DIR": "cache/numba", "MPLCONFIGDIR": "cache/matplotlib",
    }.items():
        path = root / leaf
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
    env["PATH"] = str(Path(config["ffmpeg"]).parent) + os.pathsep + env.get("PATH", "")
    if config.get("device", "cpu") == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
    return env


def start(config_path: Path, *, operation: str, audio_path: str, expected_sha256: str,
          language="ja", lyrics: str | None = None, crop_start=0.0, crop_end: float | None = None):
    config = load_config(config_path)
    if operation not in OPERATIONS or language not in ("zh", "ja", "en", "yue"):
        raise AudioError("INVALID_REQUEST", "Unknown operation or language")
    source = Path(audio_path).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in AUDIO_SUFFIXES:
        raise AudioError("AUDIO_REQUIRED", "Supply a local audio file")
    if not all(math.isfinite(v) for v in (crop_start, crop_end if crop_end is not None else crop_start)):
        raise AudioError("INVALID_CROP", "Crop values must be finite")
    if crop_start < 0 or (crop_end is not None and (crop_end <= crop_start or crop_end - crop_start > 60)):
        raise AudioError("INVALID_CROP", "Choose an ordered crop of at most 60 seconds")
    if operation in ("align", "prepare_score") and (not lyrics or not lyrics.strip()):
        raise AudioError("LYRICS_REQUIRED", "Provide confirmed lyrics for this exact crop")
    if operation in ("align", "prepare_score") and language not in config["tifa"].get("g2p", {}):
        raise AudioError("LANGUAGE_NOT_PROVISIONED", "Provision this language before starting an alignment job")
    if lyrics and (len(lyrics) > 10000 or "\0" in lyrics):
        raise AudioError("INVALID_LYRICS", "Lyrics are too long or contain NUL")
    actual = sha256(source)
    if actual != expected_sha256.lower():
        raise AudioError("INPUT_CHANGED", "Inspect the audio again; input SHA-256 changed")
    identifier = uuid.uuid4().hex
    folder = job_directory(config, identifier)
    folder.mkdir(parents=True, exist_ok=False)
    copied = folder / ("input" + source.suffix.lower())
    shutil.copyfile(source, copied)
    if sha256(copied) != actual or sha256(source) != actual:
        raise AudioError("INPUT_CHANGED", "Input changed during the snapshot; no model started")
    request = {"operation": operation, "input": describe(copied), "language": language,
               "lyrics": lyrics, "crop_start": crop_start, "crop_end": crop_end}
    write_json(folder / "request.json", request)
    write_json(folder / "status.json", {"id": identifier, "state": "queued", "stage": "queued", "created": now()})
    log = (folder / "worker.log").open("ab")
    try:
        child = subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", "-m",
                                  "vocal_to_score_mcp.worker", "--config", str(config_path.resolve()),
                                  "--job", identifier], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 cwd=folder, env=model_environment(config),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        write_json(folder / "status.json", {"id": identifier, "state": "failed", "error": "Worker could not start"})
        raise
    finally:
        log.close()
    write_json(folder / "process.json", {"pid": child.pid, "started": now()})
    return {"id": identifier, "state": "queued", "folder": str(folder), "input": request["input"]}


def status(config_path: Path, identifier: str):
    folder = job_directory(load_config(config_path), identifier)
    result = read_json(folder / "status.json")
    if result["state"] == "succeeded":
        result["result"] = read_json(folder / "result.json")
    result["log"] = str(folder / "worker.log")
    return result


def cancel(config_path: Path, identifier: str):
    folder = job_directory(load_config(config_path), identifier)
    current = read_json(folder / "status.json")
    if current["state"] in ("succeeded", "failed", "cancelled"):
        return current
    (folder / "cancel.requested").touch(exist_ok=True)
    return {"id": identifier, "state": "cancellation_requested"}
