"""Small MCP facade. Model imports and DAW operations are absent here."""

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, jobs
from .storage import AudioError, describe, job_directory, load_config, read_json, sha256
from .worker import probe

mcp = MCPServer("vocal-to-score", instructions=(
    "Offline, local vocal separation, GAME singing transcription and TIFA forced alignment. "
    "Inspect audio first and pass its SHA-256. Confirm lyrics for the exact crop. First release limits each job to 60 seconds. "
    "Start returns a job ID, not a finished score. Poll status; cancelled or failed jobs are not successful. "
    "requires_review remains true after model inference. TIFA phonemes are not Vocaloid phonemes. "
    "This server never launches, controls or imports into Cubase/Piapro. Use separately verified DAW tools and copies afterwards."
))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def config_path():
    value = os.environ.get("VOCAL_TO_SCORE_CONFIG")
    if not value:
        raise AudioError("CONFIG_REQUIRED", "Set VOCAL_TO_SCORE_CONFIG to the provisioned local JSON")
    return Path(value).resolve(strict=True)


@mcp.tool(annotations=READ, structured_output=True)
def get_audio_capabilities() -> dict[str, Any]:
    """Report configured offline backends, languages and limits without importing any model."""
    config = load_config(config_path())
    return {"version": __version__, "operations": sorted(jobs.OPERATIONS), "max_crop_seconds": 60,
            "device": config.get("device", "cpu"), "languages": sorted(config["tifa"].get("g2p", {})),
            "backends": {name: {"source_revision": config[name]["revision"], "model_sha256": config[name]["sha256"]}
                         for name in ("separation", "game", "tifa")},
            "daw_access": False, "automatic_runtime_downloads": False}


@mcp.tool(annotations=READ, structured_output=True)
def inspect_audio(path: str) -> dict[str, Any]:
    """Read local audio identity and duration; this does not download or separate it."""
    source = Path(path).resolve(strict=True)
    if source.suffix.lower() not in jobs.AUDIO_SUFFIXES:
        raise AudioError("AUDIO_REQUIRED", "Supply a local audio file")
    return {**describe(source), **probe(load_config(config_path()), source)}


@mcp.tool(annotations=WRITE, structured_output=True)
def start_vocal_job(operation: str, audio_path: str, expected_sha256: str, language: str = "ja",
                    lyrics: str | None = None, crop_start: float = 0.0, crop_end: float | None = None) -> dict[str, Any]:
    """Start separate, transcribe, align, or prepare_score (all three) in an E-drive copy. Lyrics must match the exact crop. No DAW action. State queued is not completion; inspect get_vocal_job."""
    return jobs.start(config_path(), operation=operation, audio_path=audio_path, expected_sha256=expected_sha256,
                      language=language, lyrics=lyrics, crop_start=crop_start, crop_end=crop_end)


@mcp.tool(annotations=READ, structured_output=True)
def get_vocal_job(job_id: str) -> dict[str, Any]:
    """Read progress or terminal results. Successful artifacts are rehashed before reporting them."""
    result = jobs.status(config_path(), job_id)
    if result["state"] == "succeeded":
        root = job_directory(load_config(config_path()), job_id)
        for artifact in result["result"]["artifacts"]:
            path = Path(artifact["path"]).resolve(strict=True)
            if not path.is_relative_to(root) or sha256(path) != artifact["sha256"]:
                raise AudioError("ARTIFACT_CHANGED", "An output changed after validation")
    return result


@mcp.tool(annotations=READ, structured_output=True)
def read_vocal_score(job_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Read transcribed notes, floating pitch, lyrics and timing; not a verified Piapro state."""
    status = get_vocal_job(job_id)
    if status["state"] != "succeeded" or not 0 <= offset or not 1 <= limit <= 2000:
        raise AudioError("SCORE_NOT_READY", "Choose a successful transcription and a valid page")
    root = job_directory(load_config(config_path()), job_id)
    score = read_json(root / "score.json")
    notes = score.pop("notes")
    return {**score, "total_notes": len(notes), "offset": offset, "notes": notes[offset:offset+limit]}


@mcp.tool(annotations=WRITE, structured_output=True)
def cancel_vocal_job(job_id: str) -> dict[str, Any]:
    """Request cancellation. Poll until cancelled; this never kills an unrelated PID or touches a DAW."""
    return jobs.cancel(config_path(), job_id)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
