"""Explicit removals must never select neighbours by their display names."""
import __future__
import ast
from pathlib import Path
import runpy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

SOURCE=Path(__file__).resolve().parents[1]/'custom_components/sigen'
select=runpy.run_path(str(SOURCE/'device_lifecycle.py'))['removal_device_ids']


class TestDeviceLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        def device(id,identifier,parent=None,name=None,owner='entry'):
            return NS(id=id, identifiers={('sigen',identifier)}, via_device_id=parent,
                      name=name,name_by_user='Custom name',config_entry_id=owner)
        self.devices=[
            device('root','entry_sigen_inverter',name='Renamed root'),
            device('pv','entry_sigen_inverter_pv1','root'),
            device('dc','entry_sigen_inverter_dc_charger','root'),
            device('other','entry_sigen_inverter_2',name='Sigen Inverter'),
            device('otherpv','entry_sigen_inverter_2_pv1','other'),
            device('ten','entry_sigen_inverter_10',name='Sigen Inverter 10'),
            device('nameless','entry_other'),
            device('shared','entry_shared',owner='other-entry'),
        ]

    async def test_exact_root_and_descendants_only_with_renamed_and_nameless_devices(self):
        result=select(self.devices,('sigen','entry_sigen_inverter'))
        self.assertEqual({'root','pv','dc'},set(result))
        self.assertEqual('root',result[-1])
        self.assertEqual(['dc'],select(self.devices,('sigen','entry_sigen_inverter_dc_charger')))
        self.assertEqual([],select(self.devices,('sigen','entry_missing')))
        self.assertEqual([],select(self.devices,('other-domain','entry_sigen_inverter')))

    def method(self,entity_registry,device_registry):
        tree=ast.parse((SOURCE/'config_flow.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SigenergyOptionsFlowHandler')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_async_remove_devices_and_entities')
        scope={'_LOGGER':Mock(),'DOMAIN':'sigen','DEVICE_TYPE_INVERTER':'inverter',
               'DEVICE_TYPE_AC_CHARGER':'ac_charger','DEVICE_TYPE_DC_CHARGER':'dc_charger',
               'removal_device_ids':select,
               'generate_device_id':lambda name:name.lower().replace(' ','_'),
               'async_get_device_registry':lambda hass:device_registry,
               'async_get_entity_registry':lambda hass:entity_registry,
               'async_entries_for_config_entry':lambda registry,entry:self.devices,
               'async_entries_for_device':lambda registry,id,**kw:[
                   NS(entity_id=id,config_entry_id='entry')] + ([
                   NS(entity_id='foreign-'+id,config_entry_id='other-entry')]
                   if getattr(self,'foreign_entities',False) else [])}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'cleanup','exec',
                     flags=__future__.annotations.compiler_flag),scope)
        return scope[method.name]

    async def test_cleanup_removes_only_own_entities_and_selected_registry_subtree(self):
        er=NS(async_remove=Mock());dr=NS(async_remove_device=Mock(),async_update_device=Mock())
        flow=NS(hass=object(),config_entry=NS(entry_id='entry'))
        await self.method(er,dr)(flow,'inverter','Sigen Inverter')
        self.assertEqual({'root','pv','dc'},{c.args[0] for c in er.async_remove.call_args_list})
        self.assertEqual({'root','pv','dc'},{c.args[0] for c in dr.async_remove_device.call_args_list})
        dr.async_update_device.assert_not_called()

    async def test_device_with_foreign_entities_is_not_deleted_as_a_side_effect(self):
        self.foreign_entities=True
        er=NS(async_remove=Mock());dr=NS(async_remove_device=Mock(),async_update_device=Mock())
        await self.method(er,dr)(NS(hass=object(),config_entry=NS(entry_id='entry')),'inverter','Sigen Inverter')
        self.assertEqual({'root','pv','dc'},{c.args[0] for c in er.async_remove.call_args_list})
        dr.async_remove_device.assert_not_called()

    async def test_other_config_entry_cannot_be_removed_even_with_a_matching_identifier(self):
        er=NS(async_remove=Mock());dr=NS(async_remove_device=Mock(),async_update_device=Mock())
        flow=NS(hass=object(),config_entry=NS(entry_id='entry'))
        await self.method(er,dr)(flow,'ac_charger','Shared')
        er.async_remove.assert_not_called()
        dr.async_remove_device.assert_not_called()
        dr.async_update_device.assert_not_called()

    async def test_unknown_root_or_role_never_removes_anything(self):
        for role,name in (('inverter','missing'),('plant','Sigen Inverter')):
            er=NS(async_remove=Mock());dr=NS(async_remove_device=Mock(),async_update_device=Mock())
            await self.method(er,dr)(NS(hass=object(),config_entry=NS(entry_id='entry')),role,name)
            er.async_remove.assert_not_called()
            dr.async_remove_device.assert_not_called()
