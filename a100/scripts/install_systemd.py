"""Render user systemd units; this command does not start any service."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path.home() / ".config/systemd/user")
    args = parser.parse_args()
    code_root = Path(os.environ.get("A100SIM_CODE_ROOT", Path(__file__).resolve().parents[1])).resolve()
    code_path = str(code_root)
    if any(character in code_path for character in "\r\n"):
        raise ValueError("Systemd paths cannot contain newlines")
    escaped = code_path.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    args.directory.mkdir(parents=True, exist_ok=True)
    for template in sorted((code_root / "systemd").glob("*.service.in")):
        destination = args.directory / template.name[:-3]
        content = template.read_text().replace("@CODE_ROOT@", escaped)
        content = content.replace("@WORKING_DIRECTORY@", code_path.replace("%", "%%"))
        destination.write_text(content, encoding="utf-8")
        print(f"Created {destination}")


if __name__ == "__main__":
    main()
