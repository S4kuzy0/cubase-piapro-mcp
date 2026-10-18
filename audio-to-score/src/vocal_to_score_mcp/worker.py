"""Finite offline worker. Child processes are owned and reaped here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from .jobs import model_environment, now
from .score import alignment, export_midi, game_dataset, parse_game_aligned, parse_game_raw
from .storage import AudioError, describe, job_directory, load_config, read_json, sha256, write_json


def probe(config, path):
    result = subprocess.run([config["ffprobe"], "-v", "error", "-select_streams", "a:0", "-show_entries",
                             "stream=sample_rate,channels:format=duration", "-of", "json", str(path)],
                            capture_output=True, check=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise AudioError("NO_AUDIO", "Input has no decodable audio stream")
    return {"duration": float(data["format"]["duration"]), **data["streams"][0]}


class Worker:
    def __init__(self, config_path, identifier):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.folder = job_directory(self.config, identifier)
        self.identifier = identifier
        self.env = model_environment(self.config)
        self.request = read_json(self.folder / "request.json")

    def update(self, state, stage, **extra):
        write_json(self.folder / "status.json", {"id": self.identifier, "state": state,
                   "stage": stage, "updated": now(), **extra})

    def check_cancel(self):
        if (self.folder / "cancel.requested").exists():
            raise AudioError("CANCELLED", "Offline job cancelled")

    def command(self, args, stage, *, cwd=None):
        self.check_cancel()
        self.update("running", stage)
        with (self.folder / (stage + ".log")).open("ab") as log:
            child = subprocess.Popen([str(arg) for arg in args], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     env=self.env, cwd=cwd or self.folder,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = time.monotonic()
            try:
                while child.poll() is None:
                    self.check_cancel()
                    if time.monotonic() - started > 1800:
                        raise AudioError("STAGE_TIMEOUT", f"{stage} exceeded its offline time limit")
                    time.sleep(0.2)
                if child.returncode:
                    raise AudioError("BACKEND_FAILED", f"{stage} exited with {child.returncode}; inspect {stage}.log")
                self.check_cancel()
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=10)

    def normalize(self):
        source = Path(self.request["input"]["path"])
        if sha256(source) != self.request["input"]["sha256"]:
            raise AudioError("INPUT_CHANGED", "The job snapshot changed")
        info = probe(self.config, source)
        start, end = self.request["crop_start"], self.request["crop_end"]
        end = info["duration"] if end is None else end
        if not 0 <= start < end <= info["duration"] + 0.01 or end - start > 60.001:
            raise AudioError("INVALID_CROP", "Select an existing audio interval of at most 60 seconds")
        target = self.folder / "source.wav"
        self.command([self.config["ffmpeg"], "-v", "error", "-nostdin", "-n", "-i", source,
                      "-ss", str(start), "-t", str(end-start), "-map", "0:a:0", "-ac", "2", "-ar", "44100",
                      "-c:a", "pcm_s16le", target], "decode")
        actual = probe(self.config, target)
        if abs(actual["duration"] - (end-start)) > 0.05:
            raise AudioError("AUDIO_TIMING", "Decoded crop has an unexpected duration")
        return target, actual["duration"]

    def verify_models(self, names):
        identities = {}
        for name in names:
            entry = self.config[name]
            for asset in [{"path": entry["model"], "sha256": entry["sha256"]}, *entry.get("assets", []), *entry.get("source_assets", [])]:
                if sha256(Path(asset["path"])) != asset["sha256"]:
                    raise AudioError("MODEL_CHANGED", f"Pinned {name} asset changed: {Path(asset['path']).name}")
            identities[name] = {"model": Path(entry["model"]).name, "sha256": entry["sha256"],
                                "source_revision": entry["revision"], "license": entry["license"]}
        return identities

    def separate(self, audio, duration):
        settings = self.config["separation"]
        self.command([settings["python"], "-B", "-X", "utf8", Path(__file__).with_name("backend.py"),
                      "--config", self.config_path, "--folder", self.folder], "separate")
        outputs = read_json(self.folder / "separation-output.json")
        stems = {}
        for name in ("vocals", "drums", "bass", "other"):
            matches = [self.folder / "separated" / value for value in outputs if Path(value).stem.lower() == name]
            if len(matches) != 1 or not matches[0].resolve().is_relative_to(self.folder.resolve()):
                raise AudioError("STEM_LAYOUT", f"Expected exactly one {name} output")
            path = matches[0]
            if abs(probe(self.config, path)["duration"] - duration) > 0.05:
                raise AudioError("STEM_TIMING", "A separated stem changed timing")
            stems[name] = path
        instrumental = self.folder / "accompaniment.wav"
        command = [self.config["ffmpeg"], "-v", "error", "-nostdin", "-n"]
        for name in ("drums", "bass", "other"):
            command += ["-i", stems[name]]
        command += ["-filter_complex", "amix=inputs=3:normalize=0", "-c:a", "pcm_f32le", instrumental]
        self.command(command, "combine_accompaniment")
        return stems["vocals"], [*stems.values(), instrumental]

    def align(self, audio, duration):
        settings = self.config["tifa"]
        language = self.request["language"]
        g2p = settings.get("g2p", {}).get(language)
        if not g2p:
            raise AudioError("LANGUAGE_NOT_PROVISIONED", f"Provision a local {language} G2P profile")
        output = self.folder / "alignment"
        output.mkdir()
        copied = output / "source.wav"
        shutil.copyfile(audio, copied)
        (output / "source.txt").write_text(self.request["lyrics"], encoding="utf8")
        self.command([settings["python"], "-B", "-X", "utf8", Path(settings["source"]) / "infer.py", copied,
                      "-m", settings["model"], "-l", language, "--g2p", g2p, "-o", output,
                      "--stat", "--batch-size", "1", "--num-workers", "0", "--oov-handling", "raise",
                      "--skip-handling", "discard"], "align", cwd=settings["source"])
        parsed = alignment(output / "source.TextGrid", duration)
        write_json(output / "alignment.json", parsed)
        return parsed, [output / "source.TextGrid", output / "alignment.json",
                        *sorted((output / "statistics").glob("*.json"))]

    def transcribe(self, audio, duration, aligned=None):
        settings = self.config["game"]
        output = self.folder / "game"
        output.mkdir()
        prefix = [settings["python"], "-B", "-X", "utf8", Path(settings["source"]) / "infer.py"]
        common = ["-m", settings["model"], "-l", self.request["language"], "--batch-size", "1", "--num-workers", "0"]
        if aligned is not None:
            (output / "wavs").mkdir()
            shutil.copyfile(audio, output / "wavs/source.wav")
            index = output / "transcriptions.csv"
            words = game_dataset(aligned, duration, index, self.request["language"])
            self.command([*prefix, "align", index, *common, "--save-path", output / "aligned.csv"],
                         "transcribe", cwd=settings["source"])
            notes = parse_game_aligned(output / "aligned.csv", words)
        else:
            shutil.copyfile(audio, output / "source.wav")
            self.command([*prefix, "extract", output / "source.wav", *common, "--output-formats", "csv",
                          "--pitch-format", "number", "--output-dir", output], "transcribe", cwd=settings["source"])
            notes = parse_game_raw(output / "source.csv")
        if notes[-1]["end"] > duration + 0.05:
            raise AudioError("SCORE_TIMING", "Transcribed notes exceed the audio duration")
        write_json(self.folder / "score.json", {"notes": notes, "tempo_bpm": 120, "ticks_per_beat": 480,
                   "source_offset_seconds": self.request["crop_start"], "lyrics_aligned": aligned is not None,
                   "timing_basis": "phonetic syllable boundaries" if aligned is not None else "acoustic note boundaries",
                   "phonemes_are_vocaloid": False, "requires_review": True})
        export_midi(notes, self.folder / "score.mid")
        return [self.folder / "score.json", self.folder / "score.mid", *sorted(output.glob("*.csv"))]

    def run(self):
        lock = Path(self.config["root"]) / "worker.lock"
        acquired = False
        try:
            with lock.open("x") as stream:
                stream.write(self.identifier)
            acquired = True
            self.check_cancel()
            operation = self.request["operation"]
            needed = {"separate": ["separation"], "align": ["tifa"], "transcribe": ["game"],
                      "prepare_score": ["separation", "tifa", "game"]}[operation]
            identities = self.verify_models(needed)
            audio, duration = self.normalize()
            artifacts = [audio]
            if operation in ("separate", "prepare_score"):
                audio, produced = self.separate(audio, duration)
                artifacts += produced
            aligned = None
            if operation in ("align", "prepare_score"):
                aligned, produced = self.align(audio, duration)
                artifacts += produced
            if operation in ("transcribe", "prepare_score"):
                artifacts += self.transcribe(audio, duration, aligned)
            self.check_cancel()
            write_json(self.folder / "result.json", {"operation": operation, "duration_seconds": duration,
                       "source_offset_seconds": self.request["crop_start"], "models": identities,
                       "artifacts": [describe(path) for path in dict.fromkeys(artifacts)],
                       "requires_review": True, "live_state_verified": False})
            self.update("succeeded", "complete")
        except Exception as error:
            code = getattr(error, "code", "OFFLINE_JOB_FAILED")
            if isinstance(error, FileExistsError) and not acquired:
                code = "WORKER_BUSY"
            self.update("cancelled" if code == "CANCELLED" else "failed", "stopped",
                        error={"code": code, "message": str(error)})
            raise
        finally:
            if acquired and lock.read_text() == self.identifier:
                lock.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    Worker(args.config, args.job).run()
