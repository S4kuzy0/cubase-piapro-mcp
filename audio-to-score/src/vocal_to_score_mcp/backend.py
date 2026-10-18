"""Executed by the model's own Python. No imports from the DAW service."""

import argparse
import json
import os
from pathlib import Path
import sys


def separate(config, folder):
    from audio_separator.separator import Separator
    import requests
    import urllib.request

    def no_download(*args, **kwargs):
        raise RuntimeError("Runtime downloads disabled; provision all model assets first")

    requests.sessions.Session.request = no_download
    urllib.request.urlopen = no_download
    settings = config["separation"]
    runner = Separator(model_file_dir=str(Path(settings["model"]).parent),
                       output_dir=str(folder / "separated"), output_format="WAV",
                       normalization_threshold=1.0, use_soundfile=True,
                       demucs_params={"segment_size": 7, "shifts": 0, "overlap": 0.25,
                                      "segments_enabled": True})
    runner.load_model(Path(settings["model"]).name)
    result = runner.separate(str(folder / "source.wav"),
                             custom_output_names={"Vocals": "vocals", "Drums": "drums",
                                                  "Bass": "bass", "Other": "other"})
    (folder / "separation-output.json").write_text(json.dumps(result), encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    separate(config, args.folder)
