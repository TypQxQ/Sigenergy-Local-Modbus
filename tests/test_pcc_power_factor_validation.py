"""PCC-only disjoint ranges must be checked before sending a command."""
import ast
import unittest
from unittest.mock import AsyncMock, patch

from modbus_connection.mock import MockModbusConnection
import test_missing_control_readings as controls
from test_sigen_components import _load_sigen_module

PCC = {'plant_pcc_power_factor_grid_import': 40157,
       'plant_pcc_power_factor_grid_export': 40158}


class TestPCCPowerFactorValidation(unittest.IsolatedAsyncioTestCase):
    entity = controls.TestMissingControlReadings.entity

    def setUp(self):
        self.scope = controls.load_controls()

    async def test_forbidden_values_never_call_the_coordinator(self):
        for key in PCC:
            for value in (-1.1, -1, -0.9999, -0.9995, -0.9991, -0.799, -0.5, 0, 0.5, 0.799, 1.001,
                          float('nan'), float('inf'), -float('inf')):
                with self.subTest(key=key, value=value):
                    e = self.entity('number', key, {key: 1})
                    e.coordinator.async_write_parameter = AsyncMock()
                    with self.assertRaises(controls.ControlError) as error:
                        await e.async_set_native_value(value)
                    self.assertEqual('invalid_pcc_power_factor', error.exception.metadata['translation_key'])
                    self.assertEqual({'value': str(value)}, error.exception.metadata['translation_placeholders'])
                    e.coordinator.async_write_parameter.assert_not_awaited()

    async def test_valid_boundaries_write_correct_signed_words(self):
        components = _load_sigen_module('sigen_components')
        devices = _load_sigen_module('sigen_devices')
        for key, address in PCC.items():
            for value, raw in ((-0.999, 0xFC19), (-0.95, 0xFC4A), (-0.8, 0xFCE0),
                               (0.8, 800), (0.95, 950), (1, 1000)):
                with self.subTest(key=key, value=value):
                    unit = MockModbusConnection().for_unit(247)
                    unit.load_raw({'holding': {address: 1000}})
                    group = devices._ComponentGroup({'parameters': components.PlantParameters(unit)}, {})
                    group.enable_incremental({}, lambda: None, 'fixture')
                    group._field_support['parameters'][key] = True
                    e = self.entity('number', key, await group.async_update())
                    async def write(role, identifier, register, value):
                        await group.async_write(register, value)
                    e.coordinator.async_write_parameter = AsyncMock(side_effect=write)
                    with patch.object(unit, 'write_register', wraps=unit.write_register) as wire:
                        await e.async_set_native_value(value)
                    wire.assert_awaited_once_with(address, raw)
                    self.assertEqual(value, (await group.async_update())[key])

    async def test_read_failure_remains_the_first_error(self):
        for key in PCC:
            e = self.entity('number', key, {key: None})
            e.coordinator.async_write_parameter = AsyncMock()
            with self.assertRaises(controls.ControlError) as error:
                await e.async_set_native_value(0.5)
            self.assertEqual('coordinator_unavailable', error.exception.metadata['translation_key'])
            e.coordinator.async_write_parameter.assert_not_awaited()

    async def test_other_power_factor_controls_keep_their_own_ranges(self):
        for key, role in (('plant_power_factor_target', 'plant'),
                          ('inverter_power_factor_adjustment', 'inverter')):
            e = self.entity('number', key, {key: 1}, role=role)
            e.coordinator.async_write_parameter = AsyncMock()
            await e.async_set_native_value(0.5)
            e.coordinator.async_write_parameter.assert_awaited_once()

    def test_native_minimum_is_representable_and_excludes_minus_one(self):
        tree = ast.parse((controls.ROOT / 'number.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(
                    kw.arg == 'key' and isinstance(kw.value, ast.Constant) and kw.value.value in PCC
                    for kw in node.keywords):
                attrs = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords
                         if kw.arg in {'native_min_value', 'native_max_value', 'native_step'}}
                self.assertEqual({'native_min_value': -0.999, 'native_max_value': 1,
                                  'native_step': 0.001}, attrs)
