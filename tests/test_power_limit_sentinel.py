"""A reported all-ones plant power limit is unknown but can still be configured."""
import unittest
from unittest.mock import AsyncMock, patch

from modbus_connection import ModbusTimeoutError
from modbus_connection.mock import MockModbusConnection
import test_missing_control_readings as controls
from test_sigen_components import _load_sigen_module

LIMITS = {
    'plant_ess_max_charging_limit': 40032,
    'plant_ess_max_discharging_limit': 40034,
    'plant_pv_max_power_limit': 40036,
    'plant_grid_point_maximum_export_limitation': 40038,
    'plant_grid_maximum_import_limitation': 40040,
    'plant_pcs_maximum_export_limitation': 40042,
    'plant_pcs_maximum_import_limitation': 40044,
}
SENTINEL = 0xFFFFFFFF


class TestPowerLimitSentinel(unittest.IsolatedAsyncioTestCase):
    entity = controls.TestMissingControlReadings.entity

    def setUp(self):
        self.scope = controls.load_controls()
        self.devices = _load_sigen_module('sigen_devices')
        self.components = _load_sigen_module('sigen_components')

    async def read_limits(self, raw=SENTINEL):
        unit = MockModbusConnection().for_unit(247)
        unit.load_raw({'holding': {word_address: value
                                   for address in LIMITS.values()
                                   for word_address, value in ((address, raw >> 16), (address + 1, raw & 0xFFFF))}})
        group = self.devices._ComponentGroup({'parameters': self.components.PlantParameters(unit)}, {})
        group.enable_incremental({}, lambda: None, 'fixture')
        group._field_support['parameters'].update({key: True for key in LIMITS})
        values = await group.async_update()
        return unit, group, values

    async def test_real_decoded_sentinel_is_unknown_but_supported_and_available(self):
        _, group, values = await self.read_limits()
        for key in LIMITS:
            with self.subTest(key=key):
                e = self.entity('number', key, values)
                self.assertEqual(4294967.295, values[key])
                self.assertIsNone(e.native_value)
                self.assertTrue(e.available)
                self.assertIs(group.is_supported(key), True)
                self.assertEqual(4294967.295, e.coordinator.data['plant'][key])

    async def test_unknown_limit_accepts_one_write_and_displays_fresh_readback(self):
        unit, group, values = await self.read_limits()
        for key, address in LIMITS.items():
            with self.subTest(key=key):
                e = self.entity('number', key, values)
                self.assertIsNone(e.native_value)
                async def write(role, identifier, register, value):
                    await group.async_write(register, value)
                    e.coordinator.data['plant'] = await group.async_update()
                e.coordinator.async_write_parameter = AsyncMock(side_effect=write)
                with patch.object(unit, 'write_registers', wraps=unit.write_registers) as wire:
                    await e.async_set_native_value(12.345)
                e.coordinator.async_write_parameter.assert_awaited_once_with('plant', None, key, 12.345)
                wire.assert_awaited_once_with(address, [0, 12345])
                self.assertTrue(e.available)
                self.assertEqual(12.345, e.native_value)

    async def test_valid_zero_and_other_limits_are_not_filtered(self):
        for raw in (0, 1, 12345, 100000, 0xFFFFFFFE):
            with self.subTest(raw=raw):
                _, _, values = await self.read_limits(raw)
                for key in LIMITS:
                    e = self.entity('number', key, values)
                    self.assertTrue(e.available)
                    self.assertEqual(values[key], e.native_value)
                    self.assertEqual(raw / 1000, e.native_value)

    async def test_real_read_failure_remains_unavailable_and_blocks_writes(self):
        unit, group, _ = await self.read_limits()
        unit.fail_read(40032, ModbusTimeoutError('mock timeout'), register_type='holding')
        values = await group.async_update()
        for key in LIMITS:
            with self.subTest(key=key):
                e = self.entity('number', key, values)
                e.coordinator.async_write_parameter = AsyncMock()
                self.assertIsNone(e.native_value)
                self.assertFalse(e.available)
                self.assertIs(group.is_supported(key), True)
                with self.assertRaises(controls.ControlError):
                    await e.async_set_native_value(5)
                e.coordinator.async_write_parameter.assert_not_awaited()

    async def test_unrelated_numbers_are_not_reinterpreted(self):
        for key in ('plant_active_power_fixed_target', 'plant_reactive_power_fixed_target'):
            e = self.entity('number', key, {key: SENTINEL / 1000})
            self.assertEqual(SENTINEL / 1000, e.native_value)
            self.assertTrue(e.available)
