"""Missing reads must never become confirmed zero/off control states (#466)."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from modbus_connection import ModbusTimeoutError
from modbus_connection.mock import MockModbusConnection
from modbus_connection.model import Component, gauge
from test_remote_ems_write_readback import _make_coordinator
from test_sigen_components import _load_sigen_module

ROOT = Path(__file__).resolve().parents[1] / 'custom_components/sigen'


class ControlError(Exception):
    def __init__(self, **kwargs):
        self.metadata = kwargs


def load_controls():
    definitions = _load_sigen_module('modbusregisterdefinitions')
    scope = {'_LOGGER': Mock(), 'DOMAIN': 'sigen', 'HomeAssistantError': ControlError,
             'DEVICE_TYPE_PLANT': 'plant', 'DEVICE_TYPE_INVERTER': 'inverter',
             'DEVICE_TYPE_AC_CHARGER': 'ac_charger', 'DEVICE_TYPE_DC_CHARGER': 'dc_charger',
             'DCChargerRunningState': definitions.DCChargerRunningState,
             'RemoteEMSControlMode': definitions.RemoteEMSControlMode,
             'get_entity_register_support': lambda hub, *args: (hub.support, ('readable',))}
    def execute(nodes, file):
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), file, 'exec'), scope)
    base = ast.parse((ROOT / 'sigen_entity.py').read_text(encoding='utf-8'))
    cls = next(n for n in base.body if isinstance(n, ast.ClassDef) and n.name == 'SigenergyEntity')
    cls.bases = []
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {'available', '_control_reading_available'}]
    execute([cls], 'sigen_entity.py')
    for filename, classname, parent in [('number.py', 'SigenergyNumber', 'NumberEntity'),
                                        ('switch.py', 'SigenergySwitch', 'SwitchEntity'),
                                        ('select.py', 'SigenergySelect', 'SelectEntity')]:
        tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
        helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith('_deprecated_')]
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == classname)
        cls.body = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name != '__init__']
        scope[parent] = type(parent, (), {})
        execute([*helpers, cls], filename)
    return scope


def description(filename, key, scope):
    tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and any(
        kw.arg == 'key' and isinstance(kw.value, ast.Constant) and kw.value.value == key for kw in n.keywords))
    attrs = {'key': key, 'available_fn': lambda *args: True}
    for kw in node.keywords:
        if kw.arg in {'register_support_keys', 'value_fn', 'is_on_fn', 'current_option_fn', 'available_fn'}:
            attrs[kw.arg] = eval(compile(ast.Expression(kw.value), filename, 'eval'), scope)
    return SimpleNamespace(**attrs)


class TestMissingControlReadings(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = load_controls()

    def entity(self, platform, key, values, role='plant', name='Plant'):
        cls = self.scope['Sigenergy' + platform.title()]
        obj = cls()
        obj.entity_description = description(platform + '.py', key, self.scope)
        obj.entity_id = platform + '.test_original'
        obj._device_type, obj._device_name, obj._pv_string_idx = role, name, None
        data = {'plant': values} if role == 'plant' else {
            {'inverter': 'inverters', 'ac_charger': 'ac_chargers', 'dc_charger': 'dc_chargers'}[role]: {name.replace(' DC Charger', ''): values}}
        obj.coordinator = SimpleNamespace(data=data, last_update_success=True)
        if role == 'dc_charger':
            data['inverters'] = {name.replace(' DC Charger', ''): {}}
        obj.hub = SimpleNamespace(incremental_discovery=True, support=True, has_register=lambda *args: True)
        return obj

    async def test_failed_number_is_unknown_unavailable_and_cannot_write(self):
        e = self.entity('number', 'plant_ess_max_charging_limit', {'plant_ess_max_charging_limit': None})
        self.assertIsNone(e.native_value)
        self.assertFalse(e.available)
        e.entity_description.set_value_fn = AsyncMock()
        with self.assertRaises(ControlError):
            await e.async_set_native_value(5)
        e.entity_description.set_value_fn.assert_not_awaited()
        e.coordinator.data['plant']['plant_ess_max_charging_limit'] = 0
        self.assertTrue(e.available)
        self.assertEqual(0, e.native_value)
        await e.async_set_native_value(5)
        e.entity_description.set_value_fn.assert_awaited_once()

    async def test_failed_ems_switch_does_not_report_off_or_accept_writes(self):
        e = self.entity('switch', 'plant_remote_ems_enable', {'plant_remote_ems_enable': None})
        self.assertIsNone(e.is_on)
        self.assertFalse(e.available)
        for method, callback in [('async_turn_on', 'turn_on_fn'), ('async_turn_off', 'turn_off_fn')]:
            fn = AsyncMock(); setattr(e.entity_description, callback, fn)
            with self.assertRaises(ControlError): await getattr(e, method)()
            fn.assert_not_awaited()
        e.coordinator.data['plant']['plant_remote_ems_enable'] = 0
        self.assertTrue(e.available)
        self.assertFalse(e.is_on)
        e.coordinator.data['plant']['plant_remote_ems_enable'] = 1
        self.assertTrue(e.is_on)

    async def test_select_missing_value_does_not_report_its_default_option(self):
        e = self.entity('select', 'plant_ess_preheating_mode', {'plant_ess_preheating_mode': None})
        self.assertIsNone(e.current_option)
        self.assertFalse(e.available)
        e.entity_description.select_option_fn = AsyncMock()
        with self.assertRaises(ControlError): await e.async_select_option('Automatic')
        e.entity_description.select_option_fn.assert_not_awaited()
        e.coordinator.data['plant']['plant_ess_preheating_mode'] = 0
        self.assertTrue(e.available)
        self.assertEqual('Automatic', e.current_option)

    async def test_missing_key_also_stays_unknown(self):
        e = self.entity('number', 'plant_ess_max_charging_limit', {})
        self.assertIsNone(e.native_value)
        self.assertFalse(e.available)

    async def test_partial_failure_keeps_healthy_sibling_control(self):
        data = {'plant_ess_max_charging_limit': None, 'plant_remote_ems_enable': 1}
        bad = self.entity('number', 'plant_ess_max_charging_limit', data)
        good = self.entity('switch', 'plant_remote_ems_enable', data)
        self.assertFalse(bad.available)
        self.assertTrue(good.available)
        self.assertTrue(good.is_on)
        self.assertIs(bad.hub.support, True)

    async def test_write_only_power_switches_read_the_running_state(self):
        for role, key, state in [('plant', 'plant_start_stop', 'plant_running_state'),
                                 ('inverter', 'inverter_start_stop', 'inverter_running_state'),
                                 ('ac_charger', 'ac_charger_start_stop', 'ac_charger_system_state')]:
            e = self.entity('switch', key, {state: 3 if role == 'ac_charger' else 1}, role=role)
            self.assertTrue(e.available)
            self.assertTrue(e.is_on)
            e.coordinator.data[next(iter(e.coordinator.data))] = {} if role == 'plant' else {'Plant': {state: None}}
            self.assertFalse(e.available)
            self.assertIsNone(e.is_on)

    async def test_legacy_dc_write_only_control_retains_telemetry_fallback(self):
        e = self.entity('switch', 'dc_charging', {'dc_charger_output_power': 0}, role='dc_charger', name='Inv')
        self.assertTrue(e.available)
        self.assertFalse(e.is_on)
        e.coordinator.data['dc_chargers']['Inv'] = {'dc_charger_vehicle_soc': 50}
        self.assertTrue(e.available)  # Write-only command still usable.
        self.assertIsNone(e.is_on)  # No false off without a state/power reading.

    async def test_base_does_not_apply_control_guard_to_calculated_sensors(self):
        e = self.entity('number', 'plant_ess_max_charging_limit', {})
        self.assertTrue(self.scope['SigenergyEntity'].available.fget(e))


class TestTotalReadFailure(unittest.IsolatedAsyncioTestCase):
    async def test_real_failed_register_block_reaches_coordinator_as_failure(self):
        devices = _load_sigen_module('sigen_devices')
        unit = MockModbusConnection().for_unit(1)
        unit.load_raw({'input': {100: 0, 101: 0}})
        component = type('AllReads', (Component,), {
            'register_space': 'input', 'first': gauge(100, 1.0), 'second': gauge(101, 1.0),
        })(unit)
        group = devices._ComponentGroup({'telemetry': component}, {})
        group.enable_incremental({}, lambda: None, 'fixture')
        group._field_support['telemetry'].update(first=True, second=True)
        unit.fail_read(100, ModbusTimeoutError('mock timeout'), register_type='input')
        c, hub, _ = _make_coordinator()
        hub.async_read_plant_data = group.async_update
        hub.get_poll_diagnostics = lambda _: group.poll_diagnostics()
        await c.async_refresh()
        self.assertFalse(c.last_update_success)
        self.assertEqual(['first', 'second'], group.poll_diagnostics()['failed_fields'])
        self.assertIs(group.is_supported('first'), True)
        unit.fail_read(100, None, register_type='input')
        await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertEqual({'first': 0, 'second': 0}, c.data['plant'])

    async def test_total_read_failure_is_failed_and_retains_last_snapshot(self):
        c, hub, _ = _make_coordinator()
        before = deepcopy(c.data)
        hub.async_read_plant_data = AsyncMock(return_value={'plant_remote_ems_enable': None})
        hub.get_poll_diagnostics = lambda _: {'status': 'partial'}
        await c.async_refresh()
        self.assertFalse(c.last_update_success)
        self.assertEqual(before, c.data)
        self.assertEqual(1, c.update_failure_count)
        self.assertEqual('error', c.latest_update_timing['status'])
        hub.async_read_plant_data.return_value = {'plant_remote_ems_enable': 0}
        hub.get_poll_diagnostics = lambda _: {'status': 'success'}
        await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertEqual(0, c.data['plant']['plant_remote_ems_enable'])

    async def test_pending_discovery_without_attempted_failures_is_not_an_outage(self):
        c, hub, _ = _make_coordinator()
        hub.async_read_plant_data = AsyncMock(return_value={'optional': None})
        hub.get_poll_diagnostics = lambda _: {'status': 'success'}
        await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertEqual(0, c.update_failure_count)

    async def test_good_zero_from_another_device_keeps_partial_poll_usable(self):
        c, hub, _ = _make_coordinator()
        hub.async_read_plant_data = AsyncMock(return_value={'plant_remote_ems_enable': None})
        hub.inverter_connections = {'Inv': {}}
        hub.async_read_inverter_data = AsyncMock(return_value={'inverter_active_power': 0})
        hub.get_poll_diagnostics = lambda device: {'status': 'partial' if device == 'plant' else 'success'}
        await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertEqual('partial', c.latest_update_timing['status'])
        self.assertEqual(0, c.data['inverters']['Inv']['inverter_active_power'])
