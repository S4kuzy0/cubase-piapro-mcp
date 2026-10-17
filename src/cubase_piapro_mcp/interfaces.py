"""Separate installed API declarations from observed running-host capabilities."""

from __future__ import annotations

from . import cubase, host_controls, live_midi, windows
from .errors import IntegrationError
from .storage import digest


def inspect_interfaces():
    executable = cubase._executable()
    declarations = executable.parent / "midiremote_factory_scripts/.api/v1/midiremote_api_v1.d.ts"
    data = declarations.read_bytes()
    text = data.decode("utf-8")
    api, _, _ = windows.api()
    version = api.GetFileVersionInfo(str(executable), "\\")
    high, low = version["FileVersionMS"], version["FileVersionLS"]
    result = {"host_version": ".".join(str(n) for n in (high >> 16, high & 65535, low >> 16, low & 65535)),
              "declarations_sha256": digest(data),
              "declared": {"direct_access_parameter_read_write": "setParameterProcessValue (" in text,
                           "plugin_assignment_manager": "trySetSlotPlugin (" in text,
                           "eq_bindings": "class MR_ChannelEQBand" in text,
                           "send_bindings": "class MR_SendSlot" in text,
                           "plugin_parameter_banks": "makeParameterValue (" in text},
              "native_plugin_assignment_runtime_verified": False,
              "parameter_transport": "cached standard host bindings; no native object-tree or UID dereference in MIDI query callbacks",
              "plugin_loading": "bounded Cubase 14 selector fallback, additive new instruments or first empty insert only",
              "observed": None}
    state = live_midi.get_state()
    result["midi_connected"] = state.get("connected", False)
    if result["midi_connected"]:
        try:
            actual = host_controls.read_selected("summary")
            result["observed"] = {"driver_build": actual["build"], "session": actual["session"],
                                  "selected": actual["selected"], "plugins": actual["plugins"]}
        except IntegrationError as exc:
            result["observed_error"] = {"code": exc.code, "reason": str(exc)}
    return result
