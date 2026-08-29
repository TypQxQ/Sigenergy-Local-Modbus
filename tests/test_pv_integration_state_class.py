"""Regression tests for PV string integration sensor state classes."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "calculated_sensor.py"
)


def _source_tree() -> ast.Module:
    return ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


class TestPVIntegrationStateClass(unittest.TestCase):
    """Ensure each PV integration sensor keeps its declared state class."""

    def test_integration_sensor_uses_description_state_class(self) -> None:
        sensor_class = _class(_source_tree(), "SigenergyIntegrationSensor")

        class_level_state_class = [
            node
            for node in sensor_class.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "_attr_state_class"
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
        ]
        self.assertEqual([], class_level_state_class)

        init = next(
            node
            for node in sensor_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        description_assignments = [
            node
            for node in ast.walk(init)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "_attr_state_class"
                for target in node.targets
            )
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "description"
            and node.value.attr == "state_class"
        ]
        self.assertEqual(1, len(description_assignments))

    def test_pv_energy_descriptions_keep_distinct_state_classes(self) -> None:
        descriptions_class = _class(_source_tree(), "SigenergyCalculatedSensors")
        descriptions_assignment = next(
            node
            for node in descriptions_class.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "PV_INTEGRATION_SENSORS"
                for target in node.targets
            )
        )

        state_classes: dict[str, str] = {}
        for description in descriptions_assignment.value.elts:
            keywords = {keyword.arg: keyword.value for keyword in description.keywords}
            key = keywords["key"]
            state_class = keywords["state_class"]
            if isinstance(key, ast.Constant) and isinstance(state_class, ast.Attribute):
                state_classes[key.value] = state_class.attr

        self.assertEqual(
            {
                "pv_string_accumulated_energy": "TOTAL",
                "pv_string_daily_energy": "TOTAL_INCREASING",
            },
            state_classes,
        )


if __name__ == "__main__":
    unittest.main()
