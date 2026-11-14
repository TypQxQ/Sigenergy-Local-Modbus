"""Keep unimplemented load-power registers from reporting the all-ones marker."""
from __future__ import annotations

import ast
from decimal import InvalidOperation
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_alarm7_unknown_value import sensor_class


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/sigen"
FUTURE = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)


def load_marker_helper():
    tree = ast.parse((COMPONENT / "common.py").read_text(encoding="utf-8"))
    helper = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "is_unimplemented_register_marker")
    module = ast.Module(body=[FUTURE, helper], type_ignores=[])
    scope = {"isfinite": isfinite}
    exec(compile(ast.fix_missing_locations(module), "load_marker", "exec"), scope)
    return scope["is_unimplemented_register_marker"]


def load_consumed_calculation():
    tree = ast.parse((COMPONENT / "calculated_sensor.py").read_text(encoding="utf-8"))
    calculations = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == "SigenergyCalculations")
    calculations.body = [node for node in calculations.body
                         if isinstance(node, ast.FunctionDef)
                         and node.name == "calculate_plant_consumed_power"]
    common = ast.parse((COMPONENT / "common.py").read_text(encoding="utf-8"))
    helpers = [node for node in common.body
               if isinstance(node, ast.FunctionDef)
               and node.name in {"safe_float", "is_unimplemented_register_marker"}]
    module = ast.Module(body=[FUTURE, *helpers, calculations], type_ignores=[])
    scope = {"InvalidOperation": InvalidOperation, "_LOGGER": Mock(), "isfinite": isfinite}
    exec(compile(ast.fix_missing_locations(module), "consumed_power", "exec"), scope)
    return scope["SigenergyCalculations"]


def load_total_load_description():
    tree = ast.parse((COMPONENT / "static_sensor.py").read_text(encoding="utf-8"))
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and any(
        keyword.arg == "key" and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "plant_total_load_power"
        for keyword in node.keywords))
    scope = {
        "SigenergySensorEntityDescription": lambda **kwargs: SimpleNamespace(**kwargs),
        "SensorDeviceClass": SimpleNamespace(POWER="power"),
        "SensorStateClass": SimpleNamespace(MEASUREMENT="measurement"),
        "UnitOfPower": SimpleNamespace(KILO_WATT="kW"),
        "is_unimplemented_register_marker": load_marker_helper(),
    }
    return eval(compile(ast.Expression(call), "total_load", "eval"), scope)


class TestLoadPowerMarkers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.is_marker = staticmethod(load_marker_helper())
        cls.calculations = load_consumed_calculation()
        cls.total_load = load_total_load_description()
        cls.sensor_type = sensor_class()

    def sensor(self, value):
        entity = self.sensor_type()
        entity.entity_description = SimpleNamespace(
            key="plant_total_load_power", device_class=None,
            value_fn=self.total_load.value_fn,
        )
        entity._device_type, entity._device_name = "plant", "plant"
        entity._round_digits = None
        entity.base_available, entity.base_attributes = True, None
        entity.coordinator = SimpleNamespace(
            data={"plant": {"plant_total_load_power": value}}
        )
        return entity

    def data(self, general_load):
        return {
            "plant": {
                "plant_active_power": 1,
                "plant_grid_sensor_active_power": 2,
                "plant_third_party_photovoltaic_power": 1,
                "plant_general_load_power": general_load,
            },
            "ac_chargers": {},
        }

    def test_marker_detection(self):
        for value in (-0.001, 4294967.295):
            with self.subTest(value=value):
                self.assertTrue(self.is_marker(value))
        for value in (None, False, 0, 0.0, 3.5, -1.0, float("nan"), float("inf"), "invalid"):
            with self.subTest(value=value):
                self.assertFalse(self.is_marker(value))

    def test_consumed_power_ignores_all_ones_direct_reading(self):
        for marker in (-0.001, 4294967.295):
            with self.subTest(marker=marker):
                calculations = self.calculations
                self.assertEqual(
                    4, calculations.calculate_plant_consumed_power(None, self.data(marker))
                )
        self.assertEqual(
            2, self.calculations.calculate_plant_consumed_power(None, self.data(2))
        )

    def test_total_load_reports_unknown_for_all_ones(self):
        value_fn = self.total_load.value_fn
        for marker in (-0.001, 4294967.295):
            with self.subTest(marker=marker):
                self.assertIsNone(value_fn(marker, {}, {}))
        self.assertEqual(3.5, value_fn(3.5, {}, {}))
        self.assertIsNone(value_fn(None, {}, {}))

    def test_total_load_entity_reports_unknown_for_all_ones(self):
        for marker in (-0.001, 4294967.295):
            with self.subTest(marker=marker):
                self.assertIsNone(self.sensor(marker).native_value)
        self.assertEqual(3.5, self.sensor(3.5).native_value)


if __name__ == "__main__":
    unittest.main()