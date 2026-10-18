import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_discovery_and_rejected_write(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"test identity; never load")
    config = {"root": str(tmp_path), "ffmpeg": sys.executable, "ffprobe": sys.executable}
    for key in ("separation", "game", "tifa"):
        config[key] = {"python": sys.executable, "source": str(tmp_path), "model": str(model),
                       "sha256": "0"*64, "revision": "fixture", "g2p": {"ja": "fixture"}}
    config_path = tmp_path / "config.local.json"
    config_path.write_text(json.dumps(config))
    source = tmp_path / "source.wav"
    source.write_bytes(b"source remains unchanged")
    async def run():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-I", "-B", "-X", "utf8", "-m", "vocal_to_score_mcp.server"],
                                       env={**os.environ, "VOCAL_TO_SCORE_CONFIG": str(config_path)})
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {item.name for item in tools.tools} == {"get_audio_capabilities", "inspect_audio", "start_vocal_job",
                                                              "get_vocal_job", "read_vocal_score", "cancel_vocal_job"}
                caps = await session.call_tool("get_audio_capabilities", {})
                assert not caps.is_error
                assert caps.structured_content["daw_access"] is False
                invalid = await session.call_tool("start_vocal_job", {"operation": "separate", "audio_path": str(source),
                                                                       "expected_sha256": "0"*64})
                assert invalid.is_error
                assert "INPUT_CHANGED" in str(invalid.content)
                assert not (tmp_path / "jobs").exists()
                invalid_id = await session.call_tool("get_vocal_job", {"job_id": "../anything"})
                assert invalid_id.is_error
    asyncio.run(run())
    assert source.read_bytes() == b"source remains unchanged"
