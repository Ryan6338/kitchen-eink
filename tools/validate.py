#!/usr/bin/env python3
"""Fast local validation for repository configuration sources."""

from __future__ import annotations

import ast
import json
import subprocess
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


def validate_yaml() -> None:
    files = sorted(ROOT.rglob("*.yaml"))
    for path in files:
        if ".esphome" in path.parts:
            continue
        try:
            list(yaml.compose_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as error:
            raise SystemExit(f"invalid YAML in {path.relative_to(ROOT)}: {error}") from error
    print(f"YAML syntax: {len(files)} files OK")


def validate_json() -> None:
    files = sorted(ROOT.rglob("*.json"))
    for path in files:
        json.loads(path.read_text(encoding="utf-8"))
    print(f"JSON syntax: {len(files)} files OK")


def validate_python() -> None:
    source = ROOT / "home-assistant" / "custom_components"
    for path in sorted(source.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            raise SystemExit(f"Python syntax error in {path.relative_to(ROOT)}: {error}") from error
    print("Python syntax: OK")


def validate_esphome_layout() -> None:
    actual = {path.name for path in (ROOT / "esphome").glob("*.yaml")}
    expected = {"kitchen-display.yaml", "kitchen-display-simulator.yaml"}
    if actual != expected:
        raise SystemExit(
            "ESPHome root must contain only device configs; "
            f"expected {sorted(expected)}, found {sorted(actual)}"
        )
    print("ESPHome dashboard layout: only real devices are at the config root")


def validate_generated_scripts() -> None:
    with tempfile.TemporaryDirectory(prefix="kitchen-eink-check-") as temp_dir:
        output = Path(temp_dir) / "kitchen_scripts.yaml"
        subprocess.run(
            [
                "python3",
                str(ROOT / "tools" / "build_script_package.py"),
                str(ROOT / "home-assistant" / "scripts"),
                str(output),
            ],
            check=True,
        )
        parsed = yaml.safe_load(output.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not parsed.get("script"):
            raise SystemExit("generated script package is empty or invalid")
        print(f"Generated script package: {len(parsed['script'])} scripts OK")


def main() -> None:
    validate_yaml()
    validate_json()
    validate_python()
    validate_esphome_layout()
    validate_generated_scripts()


if __name__ == "__main__":
    main()
