from pathlib import Path
import shutil
import subprocess

import pytest


def test_driver_queries_do_not_dereference_native_or_empty_host_channels():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed only for the JavaScript driver contract test")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("driver_host_contract.cjs")),
         str(root / "src/cubase_piapro_mcp/assets/LocalStudio_CubasePiaproMCP.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
