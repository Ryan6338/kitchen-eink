#!/usr/bin/env python3
"""Remove source-controlled script blocks from Home Assistant's scripts.yaml."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


TOP_LEVEL_KEY = re.compile(r"^([A-Za-z0-9_]+):(?:\s*(?:#.*)?)?$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scripts_yaml", type=Path)
    parser.add_argument("scripts_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    managed_ids = {
        path.stem.replace("-", "_") for path in args.scripts_dir.glob("*.yaml")
    }
    source = args.scripts_yaml.read_text(encoding="utf-8") if args.scripts_yaml.exists() else ""

    output: list[str] = []
    skipping = False
    removed: set[str] = set()
    for line in source.splitlines(keepends=True):
        match = TOP_LEVEL_KEY.match(line.rstrip("\r\n"))
        if match:
            key = match.group(1)
            skipping = key in managed_ids
            if skipping:
                removed.add(key)
                continue
        if not skipping:
            output.append(line)

    args.output.write_text("".join(output), encoding="utf-8")
    if removed:
        print("Migrating managed scripts: " + ", ".join(sorted(removed)))


if __name__ == "__main__":
    main()
