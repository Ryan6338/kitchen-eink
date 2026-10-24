#!/usr/bin/env python3
"""Fast local validation for repository configuration sources."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


class UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects keys PyYAML would otherwise overwrite."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _construct_custom_tag(loader: UniqueKeyLoader, node: yaml.Node) -> object:
    """Construct Home Assistant and ESPHome tagged values for syntax checks."""

    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    raise yaml.constructor.ConstructorError(
        None, None, "unsupported YAML node", node.start_mark
    )


UniqueKeyLoader.add_constructor(None, _construct_custom_tag)


def validate_yaml() -> None:
    files = sorted(
        path for path in ROOT.rglob("*.yaml") if ".esphome" not in path.parts
    )
    for path in files:
        try:
            list(
                yaml.load_all(
                    path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader
                )
            )
        except yaml.YAMLError as error:
            raise SystemExit(f"invalid YAML in {path.relative_to(ROOT)}: {error}") from error
    print(f"YAML syntax: {len(files)} files OK")


def validate_json() -> None:
    files = sorted(
        path for path in ROOT.rglob("*.json") if ".esphome" not in path.parts
    )
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
    expected = {
        "evie-office-display.yaml",
        "evie-office-display-simulator.yaml",
        "kitchen-display.yaml",
        "kitchen-display-simulator.yaml",
    }
    if actual != expected:
        raise SystemExit(
            "ESPHome root must contain only device and simulator entries; "
            f"expected {sorted(expected)}, found {sorted(actual)}"
        )
    print("ESPHome dashboard layout: only device and simulator entries are at the config root")


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


def validate_unit_tests() -> None:
    subprocess.run(
        [
            "python3",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(ROOT / "tests"),
            "-p",
            "test_*.py",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True,
    )
    print("Meal planner unit tests: OK")


def main() -> None:
    validate_yaml()
    validate_json()
    validate_python()
    validate_esphome_layout()
    validate_generated_scripts()
    validate_unit_tests()


if __name__ == "__main__":
    main()
