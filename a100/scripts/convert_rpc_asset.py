"""Convert one uploaded GLB into a new immutable gateway asset directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from prepare_fasttrellis_assets import convert_asset


def main() -> None:
    """Write converted mesh data and a report containing its public asset handle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source-up", choices=("y", "z"), default="y")
    parser.add_argument("--max-size", type=float, default=1.2)
    parser.add_argument("--strict-materials", action="store_true")
    args = parser.parse_args()
    if args.source.suffix.lower() != ".glb" or not 0 < args.max_size <= 100:
        parser.error("Provide a GLB and max-size between 0 and 100 meters")
    result = convert_asset(
        args.source, args.destination, args.source_up, args.max_size, args.destination.name, args.strict_materials
    )
    result.pop("source", None)
    result.pop("asset_directory", None)
    result.update(success=True, asset_id=args.destination.name)
    args.report.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
