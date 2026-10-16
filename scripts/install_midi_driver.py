"""Install only this bridge's MIDI Remote script in the current user's Documents."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cubase_piapro_mcp.storage import digest, read_file, write_result, state_dir


def main():
    from win32com.shell import shell, shellcon

    documents = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PERSONAL, None, 0))
    script = ROOT / "src/cubase_piapro_mcp/assets/LocalStudio_CubasePiaproMCP.js"
    directory = (
        documents
        / "Steinberg/Cubase/MIDI Remote/Driver Scripts/Local/LocalStudio/CubasePiaproMCP"
    )
    target = directory / script.name
    data = script.read_bytes()
    if target.exists() and target.read_bytes() == data:
        result = {
            "path": str(target),
            "sha256": digest(data),
            "already_installed": True,
        }
    elif target.exists():
        source, _, sha = read_file(target)
        result = write_result(source, str(target), data, sha)
    else:
        result = write_result(None, str(target), data)
    result["next_step"] = (
        "Create CP MCP To Cubase / CP MCP From Cubase loopback ports, then reload MIDI Remote scripts or restart Cubase."
    )
    (state_dir() / "midi-driver-install.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
