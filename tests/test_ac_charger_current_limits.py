"""AC current limits follow each charger's ratings, not a fixed 32 A (#479)."""
import unittest
from unittest.mock import AsyncMock

import test_missing_control_readings as controls
from test_sigen_components import _load_sigen_module
from modbus_connection.mock import MockModbusConnection


class TestACChargerCurrentLimits(unittest.IsolatedAsyncioTestCase):
    entity = controls.TestMissingControlReadings.entity

    def setUp(self):
        self.scope = controls.load_controls()

    def charger(self, rated=48, breaker=60):
        return self.entity('number', 'ac_charger_output_current', {
            'ac_charger_output_current': 6,
            'ac_charger_rated_current': rated,
            'ac_charger_input_breaker_rated_current': breaker,
        }, role='ac_charger', name='Charger')

    async def test_smaller_rating_controls_display_and_write_boundary(self):
        for rated, breaker, maximum in ((32, 32, 32), (48, 60, 48), (48, 40, 40),
                                         (16, 32, 16), (60, 48, 48), (6, 6, 6),
                                         (48, 40.5, 40.5)):
            with self.subTest(rated=rated, breaker=breaker):
                e = self.charger(rated, breaker)
                self.assertTrue(e.available)
                self.assertEqual(maximum, e.native_max_value)
                e.coordinator.async_write_parameter = AsyncMock()
                for value in (6, maximum):
                    await e.async_set_native_value(value)
                    e.coordinator.async_write_parameter.assert_awaited_with(
                        'ac_charger', 'Charger', 'ac_charger_output_current', value)
                e.coordinator.async_write_parameter.reset_mock()
                for value in (5, maximum + 0.01, float('nan'), float('inf'), -float('inf'), True):
                    with self.assertRaises(controls.ControlError) as error:
                        await e.async_set_native_value(value)
                    self.assertEqual('invalid_ac_charger_current', error.exception.metadata['translation_key'])
                e.coordinator.async_write_parameter.assert_not_awaited()

    async def test_unknown_or_invalid_limits_block_writes_and_recover(self):
        for key in ('ac_charger_rated_current', 'ac_charger_input_breaker_rated_current'):
            for value in (None, 0, -0.01, 5.99, True, '48', float('nan'), float('inf'),
                          -float('inf'), 0x7FFFFFFF / 100, 1e100):
                with self.subTest(key=key, value=value):
                    e = self.charger()
                    data = e.coordinator.data['ac_chargers']['Charger']
                    data[key] = value
                    self.assertFalse(e.available)
                    self.assertEqual(6, e.native_max_value)
                    e.coordinator.async_write_parameter = AsyncMock()
                    with self.assertRaises(controls.ControlError):
                        await e.async_set_native_value(48)
                    e.coordinator.async_write_parameter.assert_not_awaited()
                    del data[key]
                    self.assertFalse(e.available)
                    data[key] = 48
                    self.assertTrue(e.available)
                    self.assertEqual(48, e.native_max_value)
                    await e.async_set_native_value(48)

    async def test_live_rating_changes_and_multiple_chargers_are_isolated(self):
        e = self.charger()
        e.coordinator.data['ac_chargers']['Other'] = {
            'ac_charger_rated_current': 16, 'ac_charger_input_breaker_rated_current': 16}
        self.assertEqual(48, e.native_max_value)
        e.coordinator.data['ac_chargers']['Charger']['ac_charger_input_breaker_rated_current'] = 16
        self.assertEqual(16, e.native_max_value)
        e.coordinator.async_write_parameter = AsyncMock()
        with self.assertRaises(controls.ControlError):
            await e.async_set_native_value(48)
        e.coordinator.async_write_parameter.assert_not_awaited()
        e.coordinator.data = None
        self.assertFalse(e.available)
        self.assertEqual(6, e.native_max_value)

    async def test_read_failure_blocks_write_even_with_good_ratings(self):
        e = self.charger()
        e.coordinator.data['ac_chargers']['Charger']['ac_charger_output_current'] = None
        self.assertFalse(e.available)
        self.assertIsNone(e.native_value)
        e.coordinator.async_write_parameter = AsyncMock()
        with self.assertRaises(controls.ControlError):
            await e.async_set_native_value(48)
        e.coordinator.async_write_parameter.assert_not_awaited()

    async def test_real_register_scaling_and_48_amp_write(self):
        components = _load_sigen_module('sigen_components')
        devices = _load_sigen_module('sigen_devices')
        unit = MockModbusConnection().for_unit(1)
        unit.load_raw({'input': {32007: 0, 32008: 4800, 32010: 0, 32011: 6000},
                       'holding': {42001: 0, 42002: 600}})
        group = devices._ComponentGroup({
            'telemetry': components.ACChargerRunningInfo(unit),
            'parameters': components.ACChargerParameters(unit),
        }, {})
        group.enable_incremental({}, lambda: None, 'fixture')
        for component, fields in (
            ('telemetry', ('ac_charger_rated_current', 'ac_charger_input_breaker_rated_current')),
            ('parameters', ('ac_charger_output_current',)),
        ):
            group._field_support[component].update({key: True for key in fields})
        e = self.charger()
        e.coordinator.data['ac_chargers']['Charger'] = await group.async_update()
        self.assertEqual(48, e.native_max_value)

        async def write(role, name, key, value):
            await group.async_write(key, value)

        e.coordinator.async_write_parameter = AsyncMock(side_effect=write)
        await e.async_set_native_value(48)
        self.assertEqual(48, (await group.async_update())['ac_charger_output_current'])

    def test_other_numbers_keep_description_maximum(self):
        parent = self.scope['NumberEntity']
        parent.native_max_value = property(lambda e: e.entity_description.native_max_value)
        e = self.entity('number', 'plant_grid_maximum_import_limitation',
                        {'plant_grid_maximum_import_limitation': 0})
        e.entity_description.native_max_value = 100
        self.assertEqual(100, e.native_max_value)
