"""Read Cubase's existing local scan caches without loading any plugin binaries."""

from __future__ import annotations

import os
import re
from pathlib import Path

from lxml import etree

from .errors import IntegrationError
from .storage import digest


def preferences():
    configured = os.environ.get("CUBASE_PIAPRO_CUBASE_PREFS")
    if configured:
        folder = Path(configured).expanduser().resolve(strict=True)
        if folder.is_dir():
            return folder
    candidates = [p for p in (Path(os.environ.get("APPDATA", "")) / "Steinberg").glob("Cubase*14*")
                  if p.is_dir() and (p / "VstPlugInfoV4.xml").is_file()]
    if len(candidates) != 1:
        raise IntegrationError("CUBASE_PLUGIN_CACHE_REQUIRED", "Set CUBASE_PIAPRO_CUBASE_PREFS to the intended Cubase 14 preferences folder; cache identity must be unique.")
    return candidates[0]


def _strings(node):
    return {item.get("name"): item.get("value", "") for item in node.findall("string")}


def read_catalog(folder):
    folder = Path(folder)
    base = folder / "VstPlugInfoV4.xml"
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        tree = etree.fromstring(base.read_bytes(), parser)
    except (OSError, etree.XMLSyntaxError) as exc:
        raise IntegrationError("CUBASE_PLUGIN_CACHE_REQUIRED", "A readable, well-formed host plugin-name cache is required.") from exc
    records = {}
    for node in tree.findall(".//obj[@class='VstPlugInfo']"):
        fields = _strings(node)
        uid, name = fields.get("uid", ""), fields.get("name", "")
        if re.fullmatch(r"[a-fA-F0-9]{32}", uid) and name and len(name) <= 128 and all(ord(c) >= 32 for c in name):
            records[uid.upper()] = {"uid": uid.upper(), "name": name, "vendor": "", "version": "",
                                    "format": "", "kind": "unknown", "metadata_source": "host name cache"}
    for path in sorted(folder.glob("*VST3 Cache/vst3plugins.xml")):
        try:
            document = etree.fromstring(path.read_bytes(), parser)
        except (OSError, etree.XMLSyntaxError) as exc:
            raise IntegrationError("INVALID_PLUGIN_CACHE", "The VST3 scan cache cannot be parsed safely.") from exc
        for plugin in document.findall("plugin"):
            for node in plugin.findall("class"):
                uid = node.findtext("cid", "").upper()
                if node.findtext("category") != "Audio Module Class" or uid not in records:
                    continue
                sub = node.findtext("subCategories", "").split("|")
                records[uid].update(vendor=node.findtext("vendor", plugin.findtext("vendor", "")),
                                    version=node.findtext("version", ""), format="VST3",
                                    kind="instrument" if "Instrument" in sub else "effect" if "Fx" in sub else "unknown",
                                    metadata_source="host VST3 scan cache")
    for path in sorted(folder.glob("Vst2xPlugin Infos*.xml")):
        # Older Cubase writes HTML-only entities in binary paths. Parse each
        # complete Vst2xPlugInfo member separately; those paths are neither used nor returned.
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'<member name="Vst2xPlugInfo">(.*?)</member>', text, re.S):
            try:
                fields = _strings(etree.fromstring(("<member>" + match[1] + "</member>").encode("utf-8"), parser))
            except etree.XMLSyntaxError as exc:
                raise IntegrationError("INVALID_PLUGIN_CACHE", "A VST2 metadata member is malformed.") from exc
            uid = fields.get("cid", "").upper()
            if uid in records:
                records[uid].update(vendor=fields.get("vendor", ""), version=fields.get("vendorVersion", ""),
                                    format=fields.get("sdkVersion", "VST2"),
                                    kind="instrument" if "Instrument" in fields.get("subCategory", "") else "effect",
                                    metadata_source="host VST2 scan cache")
    plugins = sorted(records.values(), key=lambda row: (row["name"].casefold(), row["uid"]))
    for entry in plugins:
        entry["id"] = "plugin:" + digest((entry["uid"] + "\n" + entry["name"]).encode("utf-8"))
        entry["selector_ambiguous"] = sum(row["name"].casefold() == entry["name"].casefold() for row in plugins) != 1
    revision = digest(__import__("json").dumps(plugins, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return {"plugins": plugins, "revision": revision,
            "source": "existing local Cubase scan/name caches; no plugin DLL was loaded",
            "activation_verified": False, "live_catalog_verified": False,
            "limitation": "Cached entries may be unavailable in this Cubase edition. Host 1.2 loading verifies visible name and host metadata, not loaded class UID. Ambiguous display names are refused."}


def list_plugins():
    return read_catalog(preferences())


def requested_plugin(catalog, plugin_id, expected_revision):
    if catalog["revision"] != expected_revision:
        raise IntegrationError("PLUGIN_CATALOG_CHANGED", "Read the local plugin catalog again before loading.")
    matches = [item for item in catalog["plugins"] if item["id"] == plugin_id]
    if len(matches) != 1 or matches[0]["selector_ambiguous"]:
        raise IntegrationError("PLUGIN_ID_UNAVAILABLE", "Choose one unambiguous plugin ID from the current cache; names never identify duplicate classes safely.")
    return matches[0]
