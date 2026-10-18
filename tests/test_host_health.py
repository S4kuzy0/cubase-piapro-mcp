import os
import struct

import pytest

from cubase_piapro_mcp import cubase, live_midi, windows
from cubase_piapro_mcp.errors import IntegrationError


@pytest.fixture
def crash_during_process(monkeypatch, tmp_path):
    from cubase_piapro_mcp import host_health

    monkeypatch.setattr(host_health, "_hosts", lambda: [{"pid": 41}])
    monkeypatch.setattr(host_health, "_process_started", lambda pid: 1_700_000_000.)
    monkeypatch.setattr(host_health, "_dump_directory", lambda: tmp_path)
    dump = tmp_path / "Cubase 14.0.40 64bit2026.10.17 23.09.51.194.dmp"
    dump.write_bytes(b"diagnostic fixture, not a real dump")
    os.utime(dump, (1_700_000_010., 1_700_000_010.))
    return host_health, dump


@pytest.mark.parametrize("operation", [
    lambda: live_midi.selected_ports(),
    lambda: cubase.project_root(),
    lambda: windows.piapro_root(),
    lambda: cubase.create_project("unused", "unused", open_project=True),
])
def test_new_host_dump_blocks_before_midi_or_live_target_access(crash_during_process, monkeypatch, operation):
    monkeypatch.setattr(live_midi, "ports", lambda: pytest.fail("No MIDI setup access after a host crash"))
    with pytest.raises(IntegrationError, match="HOST_CRASH_DETECTED"):
        operation()


def test_old_dump_does_not_block_a_restarted_host(crash_during_process, monkeypatch):
    health, dump = crash_during_process
    monkeypatch.setattr(health, "_process_started", lambda pid: 1_700_000_020.)
    health.require_healthy()


def test_health_read_failure_is_not_treated_as_safe(crash_during_process, monkeypatch):
    health, dump = crash_during_process
    def denied(pid):
        raise PermissionError("Cannot read host creation time")
    monkeypatch.setattr(health, "_process_started", denied)
    with pytest.raises(IntegrationError, match="HOST_HEALTH_UNVERIFIED"):
        health.require_healthy()


def test_no_host_allows_offline_midi_setup_inspection(monkeypatch):
    from cubase_piapro_mcp import host_health
    monkeypatch.setattr(host_health, "_hosts", lambda: [])
    monkeypatch.setattr(host_health, "_dump_directory", lambda: pytest.fail("No host needs no dump lookup"))
    host_health.require_healthy()


def test_new_dump_is_checked_again_before_each_message(crash_during_process, monkeypatch):
    from types import SimpleNamespace
    health, dump = crash_during_process
    monkeypatch.setattr(health, "_process_started", lambda pid: 1_700_000_020.)
    sent = []
    output = SimpleNamespace(send=sent.append)
    live_midi.send_to_host(output, "before fault")
    os.utime(dump, (1_700_000_030., 1_700_000_030.))
    with pytest.raises(IntegrationError, match="HOST_CRASH_DETECTED"):
        live_midi.send_to_host(output, "must not be sent")
    assert sent == ["before fault"]


def test_crash_between_snapshot_and_dispatch_never_sends_or_recovers(crash_during_process, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from cubase_piapro_mcp import host_controls as hc
    from test_host_controls import raw_snapshot, transaction_env

    health, dump = crash_during_process
    monkeypatch.setattr(health, "_process_started", lambda pid: 1_700_000_020.)
    before = transaction_env(monkeypatch, tmp_path)
    monkeypatch.setattr(live_midi, "selected_ports", lambda: ("in", "out"))
    monkeypatch.setattr(hc.mido, "open_input", lambda *args: nullcontext(object()))
    output = SimpleNamespace(send=lambda message: pytest.fail("Payload must not reach the output"))
    monkeypatch.setattr(hc.mido, "open_output", lambda *args: nullcontext(output))
    def crash_after_read(*args):
        os.utime(dump, (1_700_000_030., 1_700_000_030.))
        return raw_snapshot()
    monkeypatch.setattr(hc, "_query", crash_after_read)
    monkeypatch.setattr(hc, "_recover", lambda *args: pytest.fail("No write was sent, so no recovery"))
    with pytest.raises(IntegrationError, match="HOST_CRASH_DETECTED"):
        hc.set_controls("uid:a", "host", before["revision"], "eq", [{"id": "eq.2.gain", "value": .75}])


def write_dump_owner(path, pid, created, modified):
    header = struct.pack("<4sIIIIIQ", b"MDMP", 0xA793, 1, 32, 0, modified, 0)
    directory = struct.pack("<III", 15, 24, 44)
    misc = struct.pack("<6I", 24, 3, pid, created, 0, 0)
    path.write_bytes(header + directory + misc)
    os.utime(path, (modified, modified))


@pytest.mark.parametrize("pid,created,current_start", [
    (42, 1_700_000_000, 1_700_000_000.),
    (41, 1_699_999_000, 1_700_000_000.),
])
def test_foreign_or_reused_pid_report_is_not_attributed_to_current_host(crash_during_process, monkeypatch, pid, created, current_start):
    health, dump = crash_during_process
    write_dump_owner(dump, pid, created, 1_700_000_010)
    monkeypatch.setattr(health, "_process_started", lambda pid: current_start)
    health.require_healthy()


def test_matching_dump_owner_blocks_and_parser_uses_misc_record(crash_during_process, monkeypatch):
    health, dump = crash_during_process
    write_dump_owner(dump, 41, 1_700_000_000, 1_700_000_010)
    assert health._dump_owner(dump) == (41, 1_700_000_000)
    with pytest.raises(IntegrationError, match="HOST_CRASH_DETECTED"):
        health.require_healthy()


def test_unreadable_report_directory_does_not_look_like_no_reports(crash_during_process, monkeypatch):
    health, dump = crash_during_process
    class UnreadableDirectory:
        def exists(self):
            return True
        def glob(self, pattern):
            return []  # Path.glob can suppress directory permission failures.
        def iterdir(self):
            raise PermissionError("Cannot enumerate crash reports")
    monkeypatch.setattr(health, "_dump_directory", lambda: UnreadableDirectory())
    with pytest.raises(IntegrationError, match="HOST_HEALTH_UNVERIFIED"):
        health.require_healthy()
