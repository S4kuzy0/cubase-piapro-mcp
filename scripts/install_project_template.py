"""Register a new local, media-free native Cubase template without distributing it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cubase_piapro_mcp import project_templates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--description", default="Local Cubase template")
    parser.add_argument("--standalone-no-external-media", action="store_true",
                        help="Confirm that the saved project has no external audio/video references; inspect it in Cubase first.")
    args = parser.parse_args()
    result = project_templates.register(args.source, args.sha256, args.id,
        args.description, standalone_no_external_media=args.standalone_no_external_media)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
