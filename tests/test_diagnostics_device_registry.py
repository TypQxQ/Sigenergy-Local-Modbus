"""Regression tests for diagnostics device-registry lookups."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "diagnostics.py"
)
DIAGNOSTIC_FUNCTIONS = {
    "async_get_config_entry_diagnostics",
    "async_get_device_diagnostics",
}


class TestDiagnosticsDeviceRegistry(unittest.TestCase):
    """Ensure diagnostics use Home Assistant's public registry helper."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))

    def test_public_helper_is_imported(self) -> None:
        imported_names = {
            alias.name
            for node in self.tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "homeassistant.helpers.device_registry"
            for alias in node.names
        }
        self.assertIn("async_entries_for_config_entry", imported_names)

    def test_diagnostics_entrypoints_use_public_helper(self) -> None:
        functions = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name in DIAGNOSTIC_FUNCTIONS
        }
        self.assertEqual(DIAGNOSTIC_FUNCTIONS, set(functions))

        for function_name, function in functions.items():
            with self.subTest(function=function_name):
                calls = [
                    node
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "async_entries_for_config_entry"
                ]
                self.assertEqual(1, len(calls))
                self.assertEqual(2, len(calls[0].args))
                registry_arg, entry_id_arg = calls[0].args
                self.assertIsInstance(registry_arg, ast.Name)
                self.assertEqual("device_registry", registry_arg.id)
                self.assertIsInstance(entry_id_arg, ast.Attribute)
                self.assertEqual("entry_id", entry_id_arg.attr)
                self.assertIsInstance(entry_id_arg.value, ast.Name)
                self.assertEqual("entry", entry_id_arg.value.id)

    def test_private_devices_container_is_not_used(self) -> None:
        private_access_lines = [
            node.lineno
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "devices"
            and isinstance(node.value, ast.Name)
            and node.value.id == "device_registry"
        ]
        self.assertEqual([], private_access_lines)


if __name__ == "__main__":
    unittest.main()
