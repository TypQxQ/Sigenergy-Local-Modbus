"""Decode signed power factor and scaled adjustment through actual models."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from modbus_connection.mock import MockModbusConnection
from test_sigen_components import _load_sigen_module
from test_missing_control_readings import load_controls, description
from types import SimpleNamespace


class TestPowerFactorDecoding(unittest.IsolatedAsyncioTestCase):
    async def test_signed_telemetry_and_single_scaled_control_readback(self):
        components = _load_sigen_module('sigen_components')
        devices = _load_sigen_module('sigen_devices')
        scope = load_controls()
        for raw, expected in [(0xFC4A, -0.95), (950, 0.95), (0, 0), (0xFC18, -1), (1000, 1)]:
            with self.subTest(raw=raw):
                unit = MockModbusConnection().for_unit(1)
                unit.load_raw({'input': {31023: raw}, 'holding': {41507: raw}})
                groups = []
                for label, cls, key in [('telemetry', components.InverterRunningInfo, 'inverter_power_factor'),
                                        ('parameters', components.InverterParameters, 'inverter_power_factor_adjustment')]:
                    g = devices._ComponentGroup({label: cls(unit)}, {})
                    g.enable_incremental({}, lambda: None, 'fixture')
                    g._field_support[label][key] = True
                    self.assertAlmostEqual(expected, (await g.async_update())[key])
                    groups.append(g)
                e = scope['SigenergyNumber']()
                e.entity_description = description('number.py', 'inverter_power_factor_adjustment', scope)
                e.entity_id, e._device_type, e._device_name = 'number.test_factor', 'inverter', 'Inv'
                e.coordinator = SimpleNamespace(data={'inverters': {'Inv': {'inverter_power_factor_adjustment': expected}}})
                self.assertEqual(expected, e.native_value)
                # The same scaled model still writes the original signed word.
                with patch.object(unit, 'write_register', wraps=unit.write_register) as write:
                    await groups[1].async_write('inverter_power_factor_adjustment', expected)
                    self.assertTrue(write.called)
                    self.assertEqual((41507, raw), write.call_args.args)
