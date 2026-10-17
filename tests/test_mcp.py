import asyncio
import json
import os
import sys
from pathlib import Path
import pytest

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def unpack(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(item.text for item in result.content if item.type == "text"))


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_real_stdio_discovery_edit_error_and_recovery(tmp_path, mode):
    async def scenario():
        env = dict(
            os.environ, PYTHONUTF8="1", CUBASE_PIAPRO_STATE_DIR=str(tmp_path / "state")
        )
        params = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "run_server.py")], env=env
        )
        source = tmp_path / "input.vsqx"
        source.write_bytes(
            (ROOT / "src/cubase_piapro_mcp/assets/blank.vsqx").read_bytes()
        )
        async with Client(params, mode=mode) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {
                "inspect_project",
                "edit_notes",
                "create_vsqx",
                "piapro_export",
                "cubase_control",
                "cubase_create_project",
                "cubase_add_track",
                "cubase_select_track",
                "cubase_load_piapro",
                "piapro_list_singers",
                "set_ppsf_singer",
                "piapro_set_singer",
                "cubase_get_midi_routing",
                "cubase_set_midi_routing",
            } <= names
            initial = await client.call_tool(
                "inspect_project", {"path": str(source), "include_notes": True}
            )
            assert not initial.is_error
            info = unpack(initial)
            target = tmp_path / "edited.vsqx"
            edited = await client.call_tool(
                "edit_notes",
                {
                    "path": str(source),
                    "output_path": str(target),
                    "expected_sha256": info["sha256"],
                    "changes": [{"id": info["notes"][0]["id"], "note_num": 72}],
                },
            )
            assert not edited.is_error
            assert target.exists()
            bad = await client.call_tool(
                "edit_notes",
                {
                    "path": str(source),
                    "output_path": str(tmp_path / "bad.vsqx"),
                    "expected_sha256": "0" * 64,
                    "changes": [{"id": "t0:p0:n0", "note_num": 1}],
                },
            )
            assert bad.is_error
            assert "FILE_CHANGED" in " ".join(
                item.text for item in bad.content if item.type == "text"
            )
            assert not (tmp_path / "bad.vsqx").exists()
            assert not (await client.call_tool("get_capabilities", {})).is_error
            templates = unpack(await client.call_tool("cubase_list_templates", {}))
            assert templates["templates"] == []
            refused = await client.call_tool("cubase_get_midi_routing", {
                "track_id": "not-a-real-host-id", "expected_revision": "stale"})
            assert refused.is_error
            assert "DIALOG_FALLBACK_REQUIRED" in str(refused)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_real_stdio_native_singer_file_contract(tmp_path, mode):
    native_path = os.environ.get("CP_TEST_PPSF")
    if not native_path:
        pytest.skip("Set CP_TEST_PPSF to a local native fixture")

    async def scenario():
        env = dict(os.environ, PYTHONUTF8="1", CUBASE_PIAPRO_STATE_DIR=str(tmp_path / "state"))
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "run_server.py")], env=env)
        async with Client(params, mode=mode) as client:
            discovered = await client.list_tools()
            assert len(discovered.tools) == 27
            result = await client.call_tool("piapro_list_singers", {"path": native_path})
            assert not result.is_error
            current = unpack(result)
            target = next(singer for singer in current["singers"] if singer["id"] != current["tracks"][0]["singer"]["id"])
            output = tmp_path / "changed.ppsf"
            result = await client.call_tool("set_ppsf_singer", {
                "path": native_path, "output_path": str(output), "expected_sha256": current["sha256"],
                "track_index": 0, "singer_id": target["id"]})
            assert not result.is_error
            written = unpack(result)
            assert written["live_state_updated"] is False
            assert written["singers"]["tracks"][0]["singer"]["id"] == target["id"]
            assert output.exists()
            assert (await client.call_tool("set_ppsf_singer", {
                "path": native_path, "output_path": str(tmp_path / "refused.ppsf"),
                "expected_sha256": "0" * 64, "track_index": 0, "singer_id": target["id"]})).is_error
            assert not (tmp_path / "refused.ppsf").exists()

    asyncio.run(scenario())
