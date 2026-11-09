"""Exercise the real calculation, description and sensor value path."""
from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation
import json
from math import isfinite
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
        'calculate_plant_consumed_power', 'calculate_battery_time_remaining',
        'battery_time_remaining_inputs'}]
    common = ast.parse((COMPONENT / 'common.py').read_text(encoding='utf-8'))
    helpers = [n for n in common.body if isinstance(n, ast.FunctionDef) and n.name in {'safe_decimal', 'safe_float'}]
    scope = {'Decimal': Decimal, 'InvalidOperation': InvalidOperation, '_LOGGER': Mock(), 'isfinite': isfinite,
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
        cls.sensor_type.extra_state_attributes.fget.__globals__['SC'] = cls.calculations

    def data(self, **changes):
        plant = {'plant_ess_soc': 80, 'plant_backup_soc': 20,
                 'plant_ess_rated_energy_capacity': 10, 'plant_general_load_power': 2}
        plant.update(changes)
        return {'plant': plant}

    def sensor(self, data):
        entity = self.sensor_type()
        entity.entity_description = self.description
        entity.entity_id = 'sensor.test_battery_time_remaining'
        entity._device_type, entity._device_name = 'plant', 'Plant'
        entity._round_digits = self.description.round_digits
        entity.coordinator = SimpleNamespace(data=data)
        entity.base_attributes = {'existing': 'preserved'}
        return entity

    def value(self, data):
        return self.sensor(data).native_value

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

    def test_fallback_requires_a_valid_reading_from_each_configured_charger(self):
        data = self.data(plant_general_load_power=None, plant_active_power=1,
                         plant_grid_sensor_active_power=2, plant_third_party_photovoltaic_power=1)
        data['ac_chargers'] = {'First': {'ac_charger_charging_power': 1}, 'Second': {}}
        sensor = self.sensor(data)
        for missing in ({}, {'ac_charger_charging_power': None}, {'ac_charger_charging_power': True},
                        {'ac_charger_charging_power': 'invalid'}, {'ac_charger_charging_power': float('nan')},
                        {'ac_charger_charging_power': float('inf')}):
            with self.subTest(reading=missing):
                data['ac_chargers']['Second'] = missing
                self.assertIsNone(sensor.native_value)
                self.assertIsNone(sensor.extra_state_attributes['consumed_power_kw'])
        data['ac_chargers']['Second'] = {'ac_charger_charging_power': 0}
        self.assertEqual(Decimal(2), sensor.native_value)
        self.assertEqual(3.0, sensor.extra_state_attributes['consumed_power_kw'])
        data['ac_chargers']['Second']['ac_charger_charging_power'] = 1
        self.assertEqual(Decimal(3), sensor.native_value)

    def test_fallback_works_without_configured_chargers(self):
        data = self.data(plant_general_load_power=None, plant_active_power=1,
                         plant_grid_sensor_active_power=2, plant_third_party_photovoltaic_power=1)
        data['ac_chargers'] = {}
        self.assertEqual(Decimal('1.5'), self.value(data))

    def test_direct_consumption_wins_over_fallback(self):
        data = self.data(plant_active_power=100, plant_grid_sensor_active_power=100,
                         plant_third_party_photovoltaic_power=100)
        data['ac_chargers'] = {'Charger': {}}
        self.assertEqual(Decimal(3), self.value(data))

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

    def test_attributes_explain_the_same_estimate_and_keep_existing_attributes(self):
        sensor = self.sensor(self.data())
        self.assertEqual({'existing': 'preserved', 'battery_soc_percent': 80.0,
                          'backup_soc_percent': 20.0, 'rated_capacity_kwh': 10.0,
                          'consumed_power_kw': 2.0, 'usable_energy_kwh': 6.0}, sensor.extra_state_attributes)
        attributes = sensor.extra_state_attributes
        self.assertEqual(float(sensor.native_value), attributes['usable_energy_kwh'] / attributes['consumed_power_kw'])
        json.dumps(attributes, allow_nan=False)

    def test_attributes_use_the_existing_consumption_fallback_excluding_ac_chargers(self):
        data = self.data(plant_general_load_power=None, plant_active_power=1,
                         plant_grid_sensor_active_power=2, plant_third_party_photovoltaic_power=1)
        data['ac_chargers'] = {'Charger': {'ac_charger_charging_power': 2}}
        sensor = self.sensor(data)
        self.assertEqual(2.0, sensor.extra_state_attributes['consumed_power_kw'])
        self.assertEqual(Decimal(3), sensor.native_value)

    def test_reported_field_example_matches_the_exposed_inputs(self):
        data = self.data(plant_ess_soc=65.4, plant_backup_soc=10,
                         plant_ess_rated_energy_capacity=32, plant_general_load_power=5.41)
        data['ac_chargers'] = {'Charger': {'ac_charger_charging_power': 6.83}}
        sensor = self.sensor(data)
        self.assertAlmostEqual(17.728, sensor.extra_state_attributes['usable_energy_kwh'])
        self.assertAlmostEqual(3.276895, float(sensor.native_value), places=6)
        self.assertEqual(5.41, sensor.extra_state_attributes['consumed_power_kw'])

    def test_attributes_preserve_valid_zeroes_and_reserve_behavior(self):
        for soc, reserve in ((0, 0), (20, 20), (19, 20)):
            sensor = self.sensor(self.data(plant_ess_soc=soc, plant_backup_soc=reserve,
                                           plant_general_load_power=0))
            self.assertEqual(0.0, sensor.extra_state_attributes['usable_energy_kwh'])
            self.assertEqual(0.0, sensor.extra_state_attributes['consumed_power_kw'])
            self.assertEqual(float(soc), sensor.extra_state_attributes['battery_soc_percent'])
            self.assertEqual(Decimal(0), sensor.native_value)
        sensor = self.sensor(self.data(plant_general_load_power=0))
        self.assertEqual(6.0, sensor.extra_state_attributes['usable_energy_kwh'])
        self.assertIsNone(sensor.native_value)

    def test_bad_battery_inputs_remain_null_without_hiding_other_valid_inputs(self):
        fields = {'plant_ess_soc': 'battery_soc_percent', 'plant_backup_soc': 'backup_soc_percent',
                  'plant_ess_rated_energy_capacity': 'rated_capacity_kwh'}
        for field, attribute in fields.items():
            for invalid in (None, 'unknown', 'unavailable', 'NaN', 'Infinity', True, -1):
                with self.subTest(field=field, invalid=invalid):
                    sensor = self.sensor(self.data(**{field: invalid}))
                    attributes = sensor.extra_state_attributes
                    self.assertIsNone(attributes[attribute])
                    self.assertIsNone(attributes['usable_energy_kwh'])
                    self.assertEqual(2.0, attributes['consumed_power_kw'])
                    self.assertIsNone(sensor.native_value)
                    json.dumps(attributes, allow_nan=False)

    def test_bad_consumption_remains_null_or_finite_and_does_not_invent_estimate(self):
        for power in (None, 'NaN', 'Infinity'):
            sensor = self.sensor(self.data(plant_general_load_power=power))
            self.assertIsNone(sensor.extra_state_attributes['consumed_power_kw'])
            self.assertEqual(6.0, sensor.extra_state_attributes['usable_energy_kwh'])
            self.assertIsNone(sensor.native_value)
            json.dumps(sensor.extra_state_attributes, allow_nan=False)
        sensor = self.sensor(self.data(plant_general_load_power=-1))
        self.assertEqual(-1.0, sensor.extra_state_attributes['consumed_power_kw'])
        self.assertIsNone(sensor.native_value)

    def test_attributes_clear_and_recover_with_current_data_without_mutating_it(self):
        from copy import deepcopy
        data = self.data()
        original = deepcopy(data)
        sensor = self.sensor(data)
        first = sensor.extra_state_attributes
        self.assertEqual(original, data)
        for missing in (None, {}, {'plant': None}):
            sensor.coordinator.data = missing
            attributes = sensor.extra_state_attributes
            self.assertTrue(all(attributes[key] is None for key in first if key != 'existing'))
        data['plant']['plant_general_load_power'] = 1
        sensor.coordinator.data = data
        self.assertEqual(1.0, sensor.extra_state_attributes['consumed_power_kw'])
        self.assertEqual(Decimal(6), sensor.native_value)
        self.assertEqual(2.0, first['consumed_power_kw'])


if __name__ == '__main__':
    unittest.main()
