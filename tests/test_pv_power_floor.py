"""Regression tests for PV string power measurement offsets."""

from __future__ import annotations

import ast
import copy
import unittest
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unittest.mock import Mock


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "calculated_sensor.py"
)


def _calculate_pv_power_function():
    """Load calculate_pv_power without requiring Home Assistant."""
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    calculations_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SigenergyCalculations"
    )
    function = copy.deepcopy(
        next(
            node
            for node in calculations_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "calculate_pv_power"
        )
    )
    function.decorator_list = []
    module = ast.fix_missing_locations(
        ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")],
                    level=0,
                ),
                function,
            ],
            type_ignores=[],
        )
    )
    namespace = {
        "Decimal": Decimal,
        "InvalidOperation": InvalidOperation,
        "_LOGGER": Mock(),
        "safe_decimal": lambda value: Decimal(str(value)),
        "safe_float": float,
    }
    exec(compile(module, SOURCE_PATH, "exec"), namespace)
    return namespace["calculate_pv_power"]


class TestPVPowerFloor(unittest.TestCase):
    """Ensure measurement offsets cannot produce negative PV power."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.calculate_pv_power = staticmethod(_calculate_pv_power_function())

    def _calculate(self, voltage: float, current: float) -> float | None:
        return self.calculate_pv_power(
            None,
            {
                "inverters": {
                    "Sigen Inverter": {
                        "inverter_pv3_voltage": voltage,
                        "inverter_pv3_current": current,
                    }
                }
            },
            {"pv_idx": 3, "device_name": "Sigen Inverter"},
        )

    def test_positive_voltage_and_current_produce_power(self) -> None:
        self.assertAlmostEqual(2.245789, self._calculate(336.7, 6.67))

    def test_non_positive_current_produces_zero_power(self) -> None:
        for current in (0.0, -0.01, -0.04):
            with self.subTest(current=current):
                self.assertEqual(0.0, self._calculate(168.6, current))

    def test_non_positive_voltage_produces_zero_power(self) -> None:
        for voltage in (0.0, -0.1):
            with self.subTest(voltage=voltage):
                self.assertEqual(0.0, self._calculate(voltage, 0.01))

    def test_negative_voltage_and_current_do_not_create_phantom_power(self) -> None:
        self.assertEqual(0.0, self._calculate(-0.1, -0.01))


if __name__ == "__main__":
    unittest.main()
