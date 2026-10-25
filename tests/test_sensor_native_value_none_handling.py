"""Regression test guarding against STATE_UNKNOWN reappearing in sensor.py.

native_value() used to return the literal string "unknown" (STATE_UNKNOWN)
instead of None for an entity description with no state_class, which crashes
Home Assistant's own SensorEntity.state for any numeric device_class (it
handles a None native_value correctly, but tries int()/float() on a literal
"unknown" string and raises). Pure AST check, matching this repo's existing
static-analysis style (see test_diagnostics_device_registry.py) -- no
Home Assistant install required.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "sensor.py"
)


class TestSensorNativeValueNoneHandling(unittest.TestCase):
    """native_value() must hand Home Assistant's own None-handling the value
    "no reading yet" actually means, not a string sentinel.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_state_unknown_is_not_imported(self) -> None:
        imported_names = {
            alias.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertNotIn("STATE_UNKNOWN", imported_names)

    def test_state_unknown_is_not_referenced_anywhere(self) -> None:
        names_used = {
            node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)
        }
        self.assertNotIn("STATE_UNKNOWN", names_used)

    def test_native_value_has_no_conditional_on_state_class_for_missing_data(self) -> None:
        """Guards against the specific pattern that caused the crash:
        `None if entity_description.state_class else <non-None sentinel>`.
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.IfExp):
                continue
            is_none_branch = isinstance(node.body, ast.Constant) and node.body.value is None
            tests_state_class = (
                isinstance(node.test, ast.Attribute) and node.test.attr == "state_class"
            )
            if is_none_branch and tests_state_class:
                self.fail(
                    f"line {node.lineno}: found a 'None if ...state_class else <other>' "
                    "conditional again -- the other branch must also be None"
                )


if __name__ == "__main__":
    unittest.main()
