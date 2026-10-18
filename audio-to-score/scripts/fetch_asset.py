"""Download a verified asset into a new E-drive folder named for its file."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile


def fetch(url: str, parent: Path, filename: str, expected_sha256: str | None):
    parent = parent.resolve(strict=True)
    if parent.drive.lower() != "e:" or Path(filename).name != filename:
        raise ValueError("Use an existing E-drive parent and a plain filename")
    folder = parent / Path(filename).stem
    folder.mkdir(exist_ok=False)
    partial = folder / (filename + ".partial")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "vocal-to-score-mcp/0.1"})
    with urllib.request.urlopen(request, timeout=90) as response, partial.open("xb") as stream:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            stream.write(chunk)
            digest.update(chunk)
    actual = digest.hexdigest()
    if expected_sha256 and actual != expected_sha256.lower():
        raise ValueError(f"SHA-256 mismatch: {actual}; partial retained")
    target = folder / filename
    partial.rename(target)
    receipt = {"url": url, "file": filename, "bytes": target.stat().st_size, "sha256": actual}
    (folder / "download.local.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    return target


def unpack(archive: Path):
    destination = archive.parent / "extracted"
    destination.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe archive member")
        bundle.extractall(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--sha256")
    parser.add_argument("--unpack", action="store_true")
    args = parser.parse_args()
    target = fetch(args.url, args.parent, args.filename, args.sha256)
    print(json.dumps({"path": str(target), "extracted": str(unpack(target)) if args.unpack else None}))
