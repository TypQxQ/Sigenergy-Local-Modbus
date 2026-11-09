"""Device addresses must be unique on an endpoint, before any probe or save."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_config_options_safety import load_flows, options_flow, vol


@unittest.skipUnless(vol is not None, "Install voluptuous for config flow tests")
class TestDeviceAddresses(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = load_flows()
        self.scope.update(vol=vol, DEVICE_CHECKS={k: k for k in ('inverter', 'ac_charger', 'dc_charger')})
        self.flow = options_flow(self.scope)
        self.flow.config_entry.options = {'entity_name_language': 'sv'}
        for group in ('inverter_connections', 'ac_charger_connections'):
            for connection in self.flow.config_entry.data[group].values():
                connection.update(host='192.0.2.1', port=502)
        self.flow._async_remove_devices_and_entities = AsyncMock()

    def connection(self, **changes):
        return {'host': '192.0.2.1', 'port': 502, 'slave_id': 1, **changes}

    async def test_address_matching_uses_all_three_parts_and_normalizes_hostname(self):
        check = self.scope['device_address_in_use']
        data = self.flow.config_entry.data
        self.assertTrue(check(data, self.connection()))
        self.assertTrue(check(data, self.connection(slave_id=2)))
        self.assertTrue(check(data, self.connection(slave_id=247)))
        for changes in ({'host': '192.0.2.2'}, {'port': 503}, {'slave_id': 3}):
            self.assertFalse(check(data, self.connection(**changes)))
        data['inverter_connections']['Fixture inverter']['host'] = 'Sigen.Example.'
        self.assertTrue(check(data, self.connection(host=' sigen.example ')))
        self.assertFalse(check(data, self.connection(host='sigen.example'),
                               ('inverter_connections', 'Fixture inverter')))
        # Excluding one device never excludes another role with the same name.
        data['ac_charger_connections']['Fixture inverter'] = self.connection(host='sigen.example')
        self.assertTrue(check(data, self.connection(host='sigen.example'),
                              ('inverter_connections', 'Fixture inverter')))

    async def test_add_charger_and_inverter_reject_collisions_without_io_or_mutation(self):
        for method in ('async_step_ac_charger_config', 'async_step_inverter_config'):
            for device_id in (1, 2):
                with self.subTest(method=method, device_id=device_id):
                    flow = self.scope['SigenergyConfigFlow']()
                    flow._selected_plant_entry_id = 'fixture'
                    flow.hass = self.flow.hass
                    flow.hass.config_entries.async_get_entry = Mock(return_value=self.flow.config_entry)
                    flow.async_show_form = self.flow.async_show_form
                    flow.async_abort = self.flow.async_abort
                    flow.async_test_connection = AsyncMock(return_value={})
                    before = deepcopy(self.flow.config_entry)
                    result = await getattr(flow, method)(self.connection(slave_id=device_id))
                    self.assertEqual({'slave_id': 'device_address_in_use'}, result['errors'])
                    flow.async_test_connection.assert_not_awaited()
                    self.assertEqual(before, self.flow.config_entry)
                    self.flow.hass.config_entries.async_update_entry.assert_not_called()
                    self.flow.hass.config_entries.async_reload.assert_not_awaited()

    async def test_add_allows_same_id_on_different_endpoint_before_testing_connection(self):
        for method in ('async_step_ac_charger_config', 'async_step_inverter_config'):
            for changes in ({'host': '192.0.2.2'}, {'port': 503}, {'slave_id': 3}):
                flow = self.scope['SigenergyConfigFlow']()
                flow._selected_plant_entry_id = 'fixture'
                flow.hass = self.flow.hass
                flow.hass.config_entries.async_get_entry = Mock(return_value=self.flow.config_entry)
                flow.async_show_form = self.flow.async_show_form
                flow.async_test_connection = AsyncMock(return_value={'base': 'cannot_connect'})
                result = await getattr(flow, method)(self.connection(**changes))
                self.assertEqual({'base': 'cannot_connect'}, result['errors'])
                flow.async_test_connection.assert_awaited_once()

    async def test_edit_rejects_other_device_address_without_saving_removing_or_reloading(self):
        for role, name, method, target in (
            ('inverter', 'Fixture inverter', 'async_step_inverter_config', 2),
            ('ac_charger', 'Fixture charger', 'async_step_ac_charger_config', 1),
        ):
            self.flow._selected_device = {'type': role, 'id': name}
            before = deepcopy(self.flow.config_entry)
            result = await getattr(self.flow, method)(self.connection(slave_id=target))
            self.assertEqual({'slave_id': 'device_address_in_use'}, result['errors'])
            self.assertEqual(before, self.flow.config_entry)
            self.flow.hass.config_entries.async_update_entry.assert_not_called()
            self.flow.hass.config_entries.async_reload.assert_not_awaited()
            self.flow._async_remove_devices_and_entities.assert_not_awaited()

    async def test_unchanged_device_can_save_without_recreation_and_preserves_options(self):
        for role, name, method, target in (
            ('inverter', 'Fixture inverter', 'async_step_inverter_config', 1),
            ('ac_charger', 'Fixture charger', 'async_step_ac_charger_config', 2),
        ):
            self.flow._selected_device = {'type': role, 'id': name}
            before = deepcopy(self.flow.config_entry)
            result = await getattr(self.flow, method)(self.connection(slave_id=target))
            self.assertEqual('create_entry', result['type'])
            self.assertEqual(before.options, result['data'])
            self.assertEqual(before, self.flow.config_entry)
            self.flow.hass.config_entries.async_update_entry.assert_not_called()
            self.flow.hass.config_entries.async_reload.assert_not_awaited()
            self.flow._async_remove_devices_and_entities.assert_not_awaited()

    async def test_changed_addresses_keep_registry_entries_and_preserve_device_options(self):
        for role,name,method,device_id,group in (
            ('inverter','Fixture inverter','async_step_inverter_config',1,'inverter_connections'),
            ('ac_charger','Fixture charger','async_step_ac_charger_config',2,'ac_charger_connections'),
        ):
            for change in ({'host':'192.0.2.9'}, {'port':1502}, {'slave_id':3}):
                flow = options_flow(self.scope)
                flow.config_entry = deepcopy(self.flow.config_entry)
                flow._selected_device = {'type':role,'id':name}
                flow._async_remove_devices_and_entities = AsyncMock()
                before = deepcopy(flow.config_entry)
                submission = self.connection(slave_id=device_id)
                submission.update(change)
                result = await getattr(flow,method)(submission)
                self.assertEqual('create_entry',result['type'])
                self.assertEqual(before.options,result['data'])
                saved=flow.hass.config_entries.async_update_entry.call_args.kwargs['data']
                self.assertEqual({**before.data[group][name], **change}, saved[group][name])
                self.assertEqual(before,flow.config_entry)
                flow._async_remove_devices_and_entities.assert_not_awaited()
                flow.hass.config_entries.async_reload.assert_awaited_once_with('fixture')
