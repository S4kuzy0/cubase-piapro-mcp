"""Run real model inference through MCP against an explicitly known fixture."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def accept(config: Path, audio: Path, lyrics: str, pitches: list[int], output: Path):
    parameters = StdioServerParameters(command=sys.executable,
        args=["-I", "-B", "-X", "utf8", "-m", "vocal_to_score_mcp.server"],
        env={**os.environ, "VOCAL_TO_SCORE_CONFIG": str(config)})
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                if result.is_error or result.structured_content is None:
                    raise RuntimeError(str(result.content))
                return result.structured_content
            capabilities = await call("get_audio_capabilities", {})
            inspected = await call("inspect_audio", {"path": str(audio)})
            invalid = await session.call_tool("start_vocal_job", {"operation": "separate", "audio_path": str(audio), "expected_sha256": "0"*64})
            assert invalid.is_error and "INPUT_CHANGED" in str(invalid.content)
            started = await call("start_vocal_job", {"operation": "prepare_score", "audio_path": str(audio),
                "expected_sha256": inspected["sha256"], "language": "ja", "lyrics": lyrics})
            previous, deadline = None, time.monotonic() + 1200
            while time.monotonic() < deadline:
                status = await call("get_vocal_job", {"job_id": started["id"]})
                if status["stage"] != previous:
                    print(json.dumps({"job": started["id"], "state": status["state"], "stage": status["stage"]}), flush=True)
                    previous = status["stage"]
                if status["state"] in ("succeeded", "failed", "cancelled"):
                    break
                await asyncio.sleep(3)
            assert status["state"] == "succeeded", status
            score = await call("read_vocal_score", {"job_id": started["id"]})
            assert [round(n["pitch"]) for n in score["notes"]] == pitches, score
            assert [n["lyric"] for n in score["notes"]] == lyrics.split(), score
            cancelled = await call("start_vocal_job", {"operation": "transcribe", "audio_path": str(audio),
                "expected_sha256": inspected["sha256"], "language": "ja"})
            for _ in range(80):
                current = await call("get_vocal_job", {"job_id": cancelled["id"]})
                if current["stage"] == "transcribe":
                    break
                await asyncio.sleep(.25)
            assert current["stage"] == "transcribe", current
            await call("cancel_vocal_job", {"job_id": cancelled["id"]})
            for _ in range(80):
                current = await call("get_vocal_job", {"job_id": cancelled["id"]})
                if current["state"] in ("cancelled", "failed", "succeeded"):
                    break
                await asyncio.sleep(.25)
            assert current["state"] == "cancelled", current
            assert (await call("inspect_audio", {"path": str(audio)}))["sha256"] == inspected["sha256"]
            report = {"offline_verified": True, "mcp_stdio_verified": True,
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "package_version": capabilities["version"], "tool_count": len((await session.list_tools()).tools),
                "pipeline_job_id": started["id"], "cancellation_job_id": cancelled["id"],
                "result": status["result"], "score": score}
            with output.open("x", encoding="utf8") as stream:
                json.dump(report, stream, indent=2, ensure_ascii=False)
            print(json.dumps({"offline_verified": True, "report": str(output)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--lyrics", required=True)
    parser.add_argument("--pitches", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(accept(args.config.resolve(), args.audio.resolve(), args.lyrics,
                       [int(v) for v in args.pitches.split(",")], args.output.resolve()))
