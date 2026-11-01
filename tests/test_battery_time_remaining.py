"""Exercise the real calculation, description and sensor value path."""
from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_alarm7_unknown_value import sensor_class

COMPONENT = Path(__file__).resolve().parents[1] / 'custom_components/sigen'


def load_calculation():
    tree = ast.parse((COMPONENT / 'calculated_sensor.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SigenergyCalculations')
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {
        'calculate_plant_consumed_power', 'calculate_battery_time_remaining'}]
    common = ast.parse((COMPONENT / 'common.py').read_text(encoding='utf-8'))
    helpers = [n for n in common.body if isinstance(n, ast.FunctionDef) and n.name in {'safe_decimal', 'safe_float'}]
    scope = {'Decimal': Decimal, 'InvalidOperation': InvalidOperation, '_LOGGER': Mock(),
             'SensorDeviceClass': SimpleNamespace(DURATION='duration'),
             'UnitOfTime': SimpleNamespace(HOURS='h'),
             'SigenergySensorEntityDescription': lambda **kw: SimpleNamespace(state_class=None, **kw)}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *helpers, cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'battery_calculation', 'exec'), scope)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and any(
        kw.arg == 'key' and isinstance(kw.value, ast.Constant) and kw.value.value == 'plant_battery_time_remaining' for kw in n.keywords))
    description = eval(compile(ast.Expression(node), 'battery_description', 'eval'), scope)
    return scope['SigenergyCalculations'], description


class TestBatteryTimeRemaining(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calculations, cls.description = load_calculation()
        cls.sensor_type = sensor_class()
        cls.sensor_type.native_value.fget.__globals__.update(Decimal=Decimal, InvalidOperation=InvalidOperation, _LOGGER=Mock())

    def data(self, **changes):
        plant = {'plant_ess_soc': 80, 'plant_backup_soc': 20,
                 'plant_ess_rated_energy_capacity': 10, 'plant_general_load_power': 2}
        plant.update(changes)
        return {'plant': plant}

    def value(self, data):
        entity = self.sensor_type()
        entity.entity_description = self.description
        entity.entity_id = 'sensor.test_battery_time_remaining'
        entity._device_type, entity._device_name = 'plant', 'Plant'
        entity._round_digits = self.description.round_digits
        entity.coordinator = SimpleNamespace(data=data)
        return entity.native_value

    def test_uses_house_consumption_during_charging_discharging_and_idle(self):
        for power in (5, -5, 0, None):
            with self.subTest(battery_power=power):
                self.assertEqual(Decimal(3), self.value(self.data(plant_ess_power=power)))

    def test_uses_unrounded_consumption_and_hours(self):
        self.assertEqual(Decimal('2.000667'), self.value(self.data(plant_general_load_power='2.999')))
        self.assertEqual('duration', self.description.device_class)
        self.assertEqual('h', self.description.native_unit_of_measurement)
        self.assertIsNone(self.description.state_class)  # Estimate, not an accumulating counter.

    def test_fallback_matches_existing_consumed_power_including_chargers(self):
        data = self.data(plant_general_load_power=None, plant_active_power=1,
                         plant_grid_sensor_active_power=2, plant_third_party_photovoltaic_power=1)
        data['ac_chargers'] = {'Charger': {'ac_charger_charging_power': 2}}
        self.assertEqual(Decimal(3), self.value(data))

    def test_direct_consumption_wins_over_fallback(self):
        self.assertEqual(Decimal(3), self.value(self.data(plant_active_power=100,
            plant_grid_sensor_active_power=100, plant_third_party_photovoltaic_power=100)))

    def test_at_or_below_reserve_is_zero(self):
        for soc in (20, 19, 0):
            self.assertEqual(Decimal(0), self.value(self.data(plant_ess_soc=soc)))

    def test_missing_or_invalid_battery_inputs_remain_unknown(self):
        for field in ('plant_ess_soc', 'plant_backup_soc', 'plant_ess_rated_energy_capacity'):
            for value in (None, 'unknown', 'unavailable', 'NaN', 'Infinity', True):
                with self.subTest(field=field, value=value):
                    self.assertIsNone(self.value(self.data(**{field: value})))
        self.assertIsNone(self.value(None))
        self.assertIsNone(self.value({}))

    def test_invalid_ranges_remain_unknown(self):
        for key, value in [('plant_ess_soc', -1), ('plant_ess_soc', 101),
                           ('plant_backup_soc', -1), ('plant_backup_soc', 101),
                           ('plant_ess_rated_energy_capacity', 0), ('plant_ess_rated_energy_capacity', -1)]:
            self.assertIsNone(self.value(self.data(**{key: value})))

    def test_no_consumption_or_invalid_consumption_remains_unknown(self):
        for power in (0, -1, None, 'NaN', 'Infinity'):
            with self.subTest(consumption=power):
                self.assertIsNone(self.value(self.data(plant_general_load_power=power)))

    def test_low_positive_consumption_still_has_an_estimate(self):
        self.assertEqual(Decimal(6000), self.value(self.data(plant_general_load_power='0.001')))

    def test_recovers_after_missing_inputs_without_reusing_old_estimate(self):
        self.assertEqual(Decimal(3), self.value(self.data()))
        self.assertIsNone(self.value(self.data(plant_general_load_power=None)))
        self.assertEqual(Decimal(6), self.value(self.data(plant_general_load_power=1)))

    def test_required_registers_cover_both_consumption_paths(self):
        required = {'plant_ess_soc', 'plant_backup_soc', 'plant_ess_rated_energy_capacity'}
        self.assertEqual(2, len(self.description.register_support_alternatives))
        for alternative in self.description.register_support_alternatives:
            self.assertLessEqual(required, set(alternative))
        self.assertIn((*('plant_ess_soc', 'plant_backup_soc', 'plant_ess_rated_energy_capacity'),
                       'plant_general_load_power'), self.description.register_support_alternatives)


if __name__ == '__main__':
    unittest.main()
