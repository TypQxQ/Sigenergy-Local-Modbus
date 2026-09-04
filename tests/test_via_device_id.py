"""Regression tests for Home Assistant device hierarchy registration."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


INTEGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "sigen"
)


def _device_info_key_files(key_name: str) -> set[str]:
    """Return files which pass a key through DeviceInfo or the registry."""
    matching_files: set[str] = set()
    for source_path in INTEGRATION_PATH.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(
                keyword.arg == key_name for keyword in node.keywords
            ):
                matching_files.add(source_path.name)
            elif isinstance(node, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == key_name
                for key in node.keys
            ):
                matching_files.add(source_path.name)
    return matching_files


class TestViaDeviceId(unittest.TestCase):
    """Ensure child devices use the current device-registry API."""

    def test_deprecated_via_device_is_not_used(self) -> None:
        self.assertEqual(set(), _device_info_key_files("via_device"))

    def test_all_device_info_paths_use_via_device_id(self) -> None:
        expected_files = {
            "__init__.py",
            "button.py",
            "number.py",
            "sensor.py",
            "sigen_entity.py",
            "switch.py",
        }
        self.assertLessEqual(
            expected_files, _device_info_key_files("via_device_id")
        )

    def test_parent_devices_are_registered_before_platform_setup(self) -> None:
        source_path = INTEGRATION_PATH / "__init__.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        setup = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_setup_entry"
        )
        register_call = next(
            node
            for node in ast.walk(setup)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_register_parent_devices"
        )
        forward_call = next(
            node
            for node in ast.walk(setup)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_forward_entry_setups"
        )
        self.assertLess(register_call.lineno, forward_call.lineno)


if __name__ == "__main__":
    unittest.main()
