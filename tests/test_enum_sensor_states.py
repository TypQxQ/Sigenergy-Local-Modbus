"""Regression tests for the enum sensors' states, options and translations.

Home Assistant rejects an enum sensor state that is not one of its declared
options, and shows a state's display label only when strings.json and the
shipped translations both carry it. These tests keep ENUM_SENSOR_STATES, the
sensor descriptions and the translation files in step, without requiring a
Home Assistant install.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

COMPONENT_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "sigen"
DESCRIPTION_SOURCES = ("static_sensor.py", "calculated_sensor.py")
TRANSLATION_FILES = ("strings.json", "translations/en.json")
RESERVED_STATES = {"unknown", "unavailable"}
STATE_KEY = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")


class _AnyAttr:
    """Stands in for a Home Assistant unit enum: any attribute access works."""

    def __getattr__(self, name: str) -> str:
        return f"stub:{name}"


def _load_register_definitions():
    """Import sigen.modbusregisterdefinitions with a stub homeassistant.const."""
    if "homeassistant.const" not in sys.modules:
        ha = types.ModuleType("homeassistant")
        ha_const = types.ModuleType("homeassistant.const")
        ha_const.PERCENTAGE = "%"
        for name in (
            "UnitOfElectricCurrent",
            "UnitOfElectricPotential",
            "UnitOfEnergy",
            "UnitOfFrequency",
            "UnitOfPower",
            "UnitOfTemperature",
        ):
            setattr(ha_const, name, _AnyAttr())
        sys.modules.setdefault("homeassistant", ha)
        sys.modules["homeassistant.const"] = ha_const
    if "sigen" not in sys.modules:
        package = types.ModuleType("sigen")
        package.__path__ = [str(COMPONENT_PATH)]
        sys.modules["sigen"] = package
    return importlib.import_module("sigen.modbusregisterdefinitions")


def _description_keywords() -> dict[str, dict[str, ast.expr]]:
    """Map each sensor description key to its keyword arguments."""
    found: dict[str, dict[str, ast.expr]] = {}
    for source in DESCRIPTION_SOURCES:
        tree = ast.parse((COMPONENT_PATH / source).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "SigenergySensorEntityDescription"
            ):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            key = keywords.get("key")
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found[key.value] = keywords
    return found


class TestEnumSensorStates(unittest.TestCase):
    """Every enum sensor declares valid, translated states."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.states = _load_register_definitions().ENUM_SENSOR_STATES
        cls.descriptions = _description_keywords()
        cls.translations = {
            name: json.loads((COMPONENT_PATH / name).read_text(encoding="utf-8"))
            for name in TRANSLATION_FILES
        }

    def test_states_are_valid_unique_and_not_reserved(self) -> None:
        for key, states in self.states.items():
            with self.subTest(sensor=key):
                values = list(states.values())
                self.assertEqual(len(values), len(set(values)))
                for state in values:
                    self.assertRegex(state, STATE_KEY)
                    self.assertNotIn(state, RESERVED_STATES)

    def test_descriptions_declare_enum_options_and_translation_key(self) -> None:
        for key in self.states:
            with self.subTest(sensor=key):
                keywords = self.descriptions.get(key)
                self.assertIsNotNone(keywords, f"no description for {key}")
                assert keywords is not None
                device_class = keywords.get("device_class")
                self.assertIsInstance(device_class, ast.Attribute)
                assert isinstance(device_class, ast.Attribute)
                self.assertEqual(device_class.attr, "ENUM")
                self.assertEqual(
                    ast.unparse(keywords["options"]), f"enum_sensor_options({key!r})"
                )
                translation_key = keywords.get("translation_key")
                self.assertIsInstance(translation_key, ast.Constant)
                assert isinstance(translation_key, ast.Constant)
                self.assertEqual(translation_key.value, key)

    def test_every_state_has_a_label_in_each_translation_file(self) -> None:
        for name, translation in self.translations.items():
            sensors = translation["entity"]["sensor"]
            for key, states in self.states.items():
                with self.subTest(file=name, sensor=key):
                    labels = sensors[key]["state"]
                    self.assertEqual(set(labels), set(states.values()))
                    for label in labels.values():
                        self.assertTrue(label.strip())

    def test_dc_charger_unavailable_does_not_shadow_entity_unavailable(self) -> None:
        dc_states = self.states["dc_charger_running_state"]
        self.assertEqual(dc_states[7], "charger_unavailable")


if __name__ == "__main__":
    unittest.main()
