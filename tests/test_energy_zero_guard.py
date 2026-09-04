"""Regression tests for protected energy zero-bounce handling."""

from __future__ import annotations

import ast
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "sensor.py"
)


def _source_tree() -> ast.Module:
    return ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def _assigned_string_set(tree: ast.Module, name: str) -> set[str]:
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )
    call = assignment.value
    assert isinstance(call, ast.Call)
    values = call.args[0]
    assert isinstance(values, ast.Set)
    return {
        element.value
        for element in values.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


def _guard_for(key: str, *, last: Decimal | None, near_reset: bool):
    """Load the guard in isolation so these tests do not require Home Assistant."""
    tree = _source_tree()
    sensor_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SigenergySensor"
    )
    guard = next(
        node
        for node in sensor_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_apply_energy_zero_guard"
    )
    module = ast.fix_missing_locations(ast.Module(body=[guard], type_ignores=[]))
    namespace = {
        "Any": object,
        "Decimal": Decimal,
        "InvalidOperation": ArithmeticError,
        "_LOGGER": Mock(),
        "_PROTECTED_DAILY_ENERGY_KEYS": _assigned_string_set(
            tree, "_PROTECTED_DAILY_ENERGY_KEYS"
        ),
        "_PROTECTED_LIFETIME_ENERGY_KEYS": _assigned_string_set(
            tree, "_PROTECTED_LIFETIME_ENERGY_KEYS"
        ),
        "dt_util": SimpleNamespace(now=lambda: datetime(2026, 9, 3, 12, 0)),
    }
    exec(compile(module, SOURCE_PATH, "exec"), namespace)

    sensor = SimpleNamespace(
        entity_description=SimpleNamespace(key=key),
        entity_id=f"sensor.{key}",
        _last_valid_daily_energy_value=last,
        _last_valid_daily_energy_date=datetime(2026, 9, 3).date(),
        _is_near_daily_reset=lambda: near_reset,
    )
    return MethodType(namespace["_apply_energy_zero_guard"], sensor), sensor


class TestEnergyZeroGuard(unittest.TestCase):
    """Ensure lifetime and daily energy counters keep distinct reset rules."""

    def test_dc_charger_total_is_protected_as_lifetime_energy(self) -> None:
        tree = _source_tree()

        self.assertIn(
            "dc_charger_total_charging_capacity",
            _assigned_string_set(tree, "_PROTECTED_LIFETIME_ENERGY_KEYS"),
        )
        self.assertNotIn(
            "dc_charger_total_charging_capacity",
            _assigned_string_set(tree, "_PROTECTED_DAILY_ENERGY_KEYS"),
        )

    def test_lifetime_zero_after_positive_is_suppressed(self) -> None:
        guard, sensor = _guard_for(
            "dc_charger_total_charging_capacity",
            last=Decimal("121.56"),
            near_reset=False,
        )

        self.assertIsNone(guard(0))
        self.assertEqual(Decimal("121.56"), sensor._last_valid_daily_energy_value)

    def test_lifetime_zero_is_suppressed_near_midnight(self) -> None:
        guard, _ = _guard_for(
            "dc_charger_total_charging_capacity",
            last=Decimal("121.56"),
            near_reset=True,
        )

        self.assertIsNone(guard(0))

    def test_daily_zero_is_accepted_near_midnight(self) -> None:
        guard, sensor = _guard_for(
            "plant_daily_pv_energy",
            last=Decimal("15.2"),
            near_reset=True,
        )

        self.assertEqual(0, guard(0))
        self.assertEqual(Decimal(0), sensor._last_valid_daily_energy_value)

    def test_positive_lifetime_value_passes_through_and_is_remembered(self) -> None:
        guard, sensor = _guard_for(
            "dc_charger_total_charging_capacity",
            last=None,
            near_reset=False,
        )

        self.assertEqual(121.56, guard(121.56))
        self.assertEqual(Decimal("121.56"), sensor._last_valid_daily_energy_value)


if __name__ == "__main__":
    unittest.main()
