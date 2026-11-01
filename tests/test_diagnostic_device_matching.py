"""Exercise both production diagnostic entry points with configured devices."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_sigen_components import _load_sigen_module

P = Path(__file__).resolve().parents[1] / 'custom_components/sigen'


class TestDiagnosticDeviceMatching(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        definitions = _load_sigen_module('modbusregisterdefinitions')
        constants = ast.parse((P / 'const.py').read_text(encoding='utf-8'))
        flag = next(n.value for n in constants.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'CONF_INVERTER_HAS_DCCHARGER' for t in n.targets))
        self.flag = ast.literal_eval(flag)
        self.devices = []
        self.entry = SimpleNamespace(entry_id='entry', as_dict=lambda: {})
        self.data = {'plant': {'plant_ess_soc': 80}, 'inverters': {},
                     'dc_chargers': {'Garage': {'dc_charger_output_power': 3}},
                     'ac_chargers': {'Wallbox': {'ac_charger_charging_power': 7}}}
        for name in ('Garage', 'Garage East', 'Roof PV Inverter'):
            self.data['inverters'][name] = {'inverter_pv_string_count': 24,
                                            'inverter_active_power': len(name)}
            for index in range(1, 25):
                self.data['inverters'][name].update({f'inverter_pv{index}_voltage': 100 + index,
                                                    f'inverter_pv{index}_current': index / 10})
        self.coordinator = SimpleNamespace(data=self.data, last_update_success=True,
            update_interval=5, last_exception=None, latest_fetch_time=0.1, largest_update_interval=0.1,
            startup_fetch_time=0.1, update_timeout_count=0, update_failure_count=0,
            partial_update_count=0, latest_update_timing={}, pv_restore_diagnostics=lambda: {})
        self.hub = SimpleNamespace(inverter_connections={'Garage': {self.flag: True},
            'Garage East': {}, 'Roof PV Inverter': {}}, ac_charger_connections={'Wallbox': {}},
            _plant_host='fixture', _plant_port=502, plant_id=247, inverter_count=3,
            ac_charger_count=1, read_only=False, probe_retry_delay=60,
            discovery_diagnostics=lambda: {}, get_register_support=lambda *args: True)
        self.hass = SimpleNamespace(data={'sigen': {'entry': {'coordinator': self.coordinator, 'hub': self.hub}}})
        self.scope = {'DOMAIN': 'sigen', 'CONF_INVERTER_HAS_DCCHARGER': self.flag,
            'TO_REDACT': set(), 'HA_VERSION': 'fixture', 'pymodbus_version': 'fixture', 'tmodbus_version': 'fixture',
            'async_get_device_registry': lambda hass: None,
            'async_entries_for_config_entry': lambda registry, entry_id: self.devices,
            'async_redact_data': lambda data, keys: data, 'unknown_alarm_diagnostics': lambda data: {},
            'async_get_connection_info': lambda hass: []}
        for name in dir(definitions):
            if name.endswith('_REGISTERS'):
                self.scope[name] = getattr(definitions, name)
        common = ast.parse((P / 'common.py').read_text(encoding='utf-8'))
        nodes = [n for n in common.body if isinstance(n, ast.FunctionDef) and n.name == 'generate_device_id']
        tree = ast.parse((P / 'diagnostics.py').read_text(encoding='utf-8'))
        nodes.extend(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(P / 'diagnostics.py'), 'exec'), self.scope)

    def device(self, identifier, display_name='Renamed display label'):
        return SimpleNamespace(name=display_name, model='fixture', manufacturer='fixture',
            sw_version='fixture', serial_number=None, identifiers={('sigen', identifier)}, connections=set())

    async def reports(self, identifier):
        device = self.device(identifier)
        self.devices[:] = [device]
        before = deepcopy(self.devices)
        entry = await self.scope['async_get_config_entry_diagnostics'](self.hass, self.entry)
        single = await self.scope['async_get_device_diagnostics'](self.hass, self.entry, device)
        self.assertEqual(before, self.devices, 'Diagnostics must not mutate registry identifiers')
        combined = next(iter(entry['all_devices'].values()))
        for key in ('device_type', 'device_identifier', 'device_data'):
            self.assertEqual(combined[key], single[key], key)
        self.assertEqual(identifier, single['device_identifier'])
        return combined, single

    async def test_all_pv_suffixes_match_exactly_in_both_reports(self):
        for index in range(1, 25):
            with self.subTest(index=index):
                combined, single = await self.reports(f'entry_garage_pv{index}')
                self.assertEqual('pv_string', single['device_type'])
                for report in (combined, single):
                    self.assertEqual(index, report['pv_string_number'])
                    self.assertEqual('Garage', report['parent_inverter'])
                    self.assertEqual(100 + index, report['device_data'][f'pv{index}_voltage'])

    async def test_custom_inverter_names_use_exact_configured_data(self):
        for name in self.hub.inverter_connections:
            with self.subTest(name=name):
                slug = self.scope['generate_device_id'](name, 'inverter')
                _, result = await self.reports(f'entry_{slug}')
                self.assertEqual('inverter', result['device_type'])
                self.assertEqual(self.data['inverters'][name], result['device_data'])

    async def test_pv_in_a_parent_name_is_not_a_string_marker(self):
        _, result = await self.reports('entry_roof_pv_inverter_pv10')
        self.assertEqual(10, result['pv_string_number'])
        self.assertEqual('Roof PV Inverter', result['parent_inverter'])

    async def test_custom_ac_and_dc_names_get_the_correct_collection(self):
        for identifier, role, expected in (
            ('entry_wallbox', 'ac_charger', {'ac_charger_charging_power': 7}),
            ('entry_garage_dc_charger', 'dc_charger', {'dc_charger_output_power': 3}),
            ('entry_plant', 'plant', {'plant_ess_soc': 80}),
        ):
            with self.subTest(identifier=identifier):
                _, result = await self.reports(identifier)
                self.assertEqual(role, result['device_type'])
                self.assertEqual(expected, result['device_data'])

    async def test_stale_cross_entry_and_malformed_identifiers_do_not_guess(self):
        for identifier in ('entry_old_inverter', 'other_garage', 'entry_garage_pv0',
                           'entry_garage_pv25', 'entry_garage_pv100', 'entry_garage_pv01',
                           'entry_garage_pv1_extra'):
            with self.subTest(identifier=identifier):
                _, result = await self.reports(identifier)
                self.assertEqual('unknown', result['device_type'])
                self.assertEqual({}, result['device_data'])

    async def test_ambiguous_identifiers_never_pick_another_devices_data(self):
        self.hub.ac_charger_connections['Garage'] = {}
        _, result = await self.reports('entry_garage')
        self.assertEqual('unknown', result['device_type'])
        self.assertEqual({}, result['device_data'])

    async def test_absent_coordinator_data_is_safe(self):
        self.coordinator.data = None
        _, result = await self.reports('entry_garage_pv10')
        self.assertEqual('pv_string', result['device_type'])
        self.assertEqual({}, result['device_data'])

    async def test_unconfigured_dc_charger_is_not_inferred_from_its_name(self):
        self.hub.inverter_connections['Garage'][self.flag] = False
        _, result = await self.reports('entry_garage_dc_charger')
        self.assertEqual('unknown', result['device_type'])
