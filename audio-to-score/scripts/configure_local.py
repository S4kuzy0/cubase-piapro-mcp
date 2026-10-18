"""Record provisioned E-drive assets; no runtime downloads or DAW changes."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def configure(root: Path, ffmpeg_dir: Path, device: str):
    root = root.resolve(strict=True)
    if root.drive.lower() != "e:":
        raise ValueError("Use an existing E-drive runtime root")
    models = root / "models/demucs"
    models.mkdir(parents=True, exist_ok=True)
    for folder, name in [("download_checks", "download_checks.json"), ("htdemucs", "htdemucs.yaml"),
                         ("955717e8-8726e21a", "955717e8-8726e21a.th")]:
        source = root / "downloads" / folder / name
        target = models / name
        if target.exists() and digest(target) != digest(source):
            raise ValueError(f"Existing provisioned asset changed: {name}")
        if not target.exists():
            shutil.copyfile(source, target)
    if not digest(models / "955717e8-8726e21a.th").startswith("8726e21a"):
        raise ValueError("Demucs published filename hash prefix mismatch")
    config = {"root": str(root), "device": device,
              "ffmpeg": str((ffmpeg_dir / "ffmpeg.exe").resolve(strict=True)),
              "ffprobe": str((ffmpeg_dir / "ffprobe.exe").resolve(strict=True))}
    for key, source_name, env, model in [
        ("separation", "python-audio-separator", "separation", models / "htdemucs.yaml"),
        ("game", "GAME", "score", next((root / "downloads/GAME-1.0-small/extracted").rglob("model.pt"))),
        ("tifa", "TIFA", "score", next((root / "downloads/TIFA-1.0-ST/extracted").rglob("model.pt"))),
    ]:
        source = root / "downloads" / source_name
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        assets = [p for p in model.parent.rglob("*") if p.is_file() and p != model]
        tracked = subprocess.check_output(["git", "-C", str(source), "ls-files"], text=True).splitlines()
        code = [source / name for name in tracked if Path(name).suffix in (".py", ".json", ".yaml", ".yml")]
        config[key] = {"python": str(root / "envs" / env / "Scripts/python.exe"),
                       "source": str(source), "revision": revision, "model": str(model),
                       "sha256": digest(model), "license": "MIT" if key == "separation" else "CC-BY-NC-SA-4.0 (weights)",
                       "source_assets": [{"path": str(p), "sha256": digest(p)} for p in code],
                       "assets": [{"path": str(p), "sha256": digest(p)} for p in sorted(assets)]}
    tifa = Path(config["tifa"]["model"]).parent
    dictionary = tifa / "dictionaries"
    profiles = root / "runtime/g2p"
    profiles.mkdir(exist_ok=True)
    ja_dir = root / "envs/score/Lib/site-packages/unidic_lite/dicdir"
    common = {"preprocessors": [{"id": "filter-punctuation"}, {"id": "strip-whitespace"}]}
    variants = {
        "zh": [{"id": "chinese-pinyin", "kwargs": {"dict_path": str(dictionary / "ds-zh-pinyin-lite.txt")}}],
        "ja": [{"id": "japanese-mecab", "kwargs": {"dict_path": str(dictionary / "japanese_dict_full.txt"),
                 "nbest": 8, "unidic_dir": str(ja_dir)}}],
        "en": [{"id": "lstm", "language": "en", "kwargs": {"dict_path": str(dictionary / "ds_cmudict-07b.txt"),
                 "model_path": str(tifa / "assets/LstmG2p-Eng"), "beam_size": 8}}],
    }
    config["tifa"]["g2p"] = {}
    for language, converters in variants.items():
        path = profiles / (language + ".yaml")
        # JSON is a strict subset of YAML, with unambiguous Windows paths.
        path.write_text(json.dumps({**common, "converters": converters}, ensure_ascii=False, indent=2), encoding="utf8")
        config["tifa"]["g2p"][language] = str(path)
    target = root / "runtime/config.local.json"
    if target.exists():
        raise FileExistsError("Retain and inspect the existing configuration before regenerating it")
    target.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf8")
    print(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ffmpeg-dir", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    configure(args.root, args.ffmpeg_dir, args.device)
