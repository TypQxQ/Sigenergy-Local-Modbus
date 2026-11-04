"""Check actual binary sensor behavior and the optional configuration flow."""
import __future__
import ast
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
import runpy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from test_config_options_safety import load_flows, options_flow, vol

SOURCE=Path(__file__).resolve().parents[1]/'custom_components/sigen'
PURE=runpy.run_path(str(SOURCE/'power_flow.py'))
OPTIONS={'power_flow_hysteresis':True,'power_flow_on_w':50,'power_flow_off_w':10}


def binary_class():
    class Base:
        def __init__(self,coordinator,**kw):
            self.coordinator=coordinator
            self.entity_description=kw['description']
            self.available=True
        def _handle_coordinator_update(self):
            self.updated=True
    scope={**PURE,'Decimal':Decimal,'InvalidOperation':InvalidOperation,'_LOGGER':Mock(),
           'callback':lambda fn:fn,'SigenergyEntity':Base,'BinarySensorEntity':type('Binary',(),{}),
           'SigenergyBinarySensorEntityDescription':lambda **kw:NS(**kw),
           'BinarySensorDeviceClass':NS(POWER='power',BATTERY_CHARGING='battery_charging')}
    common=ast.parse((SOURCE/'common.py').read_text(encoding='utf-8'))
    nodes=[n for n in common.body if isinstance(n,ast.FunctionDef) and n.name in ('safe_float','safe_decimal')]
    calc=ast.parse((SOURCE/'calculated_sensor.py').read_text(encoding='utf-8'))
    cls=next(n for n in calc.body if isinstance(n,ast.ClassDef) and n.name=='SigenergyCalculations')
    cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='calculate_total_pv_power']
    nodes.append(cls)
    tree=ast.parse((SOURCE/'binary_sensor.py').read_text(encoding='utf-8'))
    nodes += [n for n in tree.body if (isinstance(n,ast.Assign) and any(
        isinstance(t,ast.Name) and t.id=='PLANT_BINARY_SENSORS' for t in n.targets))
        or (isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='PLANT_BINARY_SENSORS')
        or (isinstance(n,ast.ClassDef) and n.name=='SigenergyBinarySensor')]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'binary_sensor','exec',
                 flags=__future__.annotations.compiler_flag),scope)
    return scope['SigenergyBinarySensor'],scope['PLANT_BINARY_SENSORS']


class TestHysteresis(unittest.TestCase):
    def entity(self,key,options=None):
        cls,descriptions=binary_class()
        coordinator=NS(data={'plant':{}},hub=NS(config_entry=NS(options=options or {})))
        entity=cls(coordinator,next(d for d in descriptions if d.key==key),'Test','plant')
        entity.entity_id='binary_sensor.fixture'
        return entity

    def reading(self,entity,kw):
        source=entity.entity_description.source_key or 'plant_sigen_photovoltaic_power'
        entity.coordinator.data['plant'][source]=kw
        return entity.is_on

    def test_disabled_keeps_the_existing_ten_watt_behavior(self):
        entity=self.entity('plant_importing_from_grid')
        for power,expected in ((0,False),(.010,False),(.011,True),(.02,True),(-.1,False)):
            self.assertEqual(expected,self.reading(entity,power))

    def test_band_retains_previous_state_and_opposite_direction_turns_off(self):
        for key,sign in (('plant_importing_from_grid',1),('plant_exporting_to_grid',-1),
                         ('plant_battery_charging',1),('plant_battery_discharging',-1),
                         ('plant_pv_generating',1)):
            entity=self.entity(key,OPTIONS)
            for power,expected in ((.03,False),(.05,False),(.051,True),(.03,True),(.01,False),(.03,False),(.06,True),(-.06,False)):
                with self.subTest(key=key,power=power):
                    self.assertEqual(expected,self.reading(entity,power*sign))

    def test_missing_and_invalid_inputs_reset_memory_without_reusing_an_on_state(self):
        for bad in (None,'unknown','NaN','Infinity',True):
            entity=self.entity('plant_importing_from_grid',OPTIONS)
            self.assertTrue(self.reading(entity,.06))
            self.assertIsNone(self.reading(entity,bad))
            self.assertFalse(self.reading(entity,.03))
        entity=self.entity('plant_importing_from_grid',OPTIONS)
        self.assertTrue(self.reading(entity,.06))
        entity.available=False
        entity._handle_coordinator_update()
        entity.available=True
        self.assertFalse(self.reading(entity,.03))

    def test_reads_are_idempotent_and_do_not_modify_power_or_options(self):
        entity=self.entity('plant_battery_charging',OPTIONS)
        self.reading(entity,.06)
        before=deepcopy(entity.coordinator)
        self.assertTrue(entity.is_on)
        self.assertTrue(entity.is_on)
        self.assertEqual(before,entity.coordinator)

    def test_invalid_saved_options_fall_back_to_legacy_behavior(self):
        for changes in ({'power_flow_on_w':'NaN'}, {'power_flow_off_w':50},
                        {'power_flow_on_w':True},{'power_flow_off_w':-1},
                        {'power_flow_hysteresis':'true'}):
            entity=self.entity('plant_importing_from_grid',{**OPTIONS,**changes})
            self.assertTrue(self.reading(entity,.02))


@unittest.skipUnless(vol is not None,'Install voluptuous')
class TestHysteresisOptions(unittest.IsolatedAsyncioTestCase):
    async def test_save_options_without_touching_device_data_or_manually_reloading(self):
        scope=load_flows();scope['vol']=vol
        flow=options_flow(scope)
        flow.config_entry.options={'entity_name_language':'sv'}
        before=deepcopy(flow.config_entry)
        result=await flow.async_step_power_flow_settings(OPTIONS)
        self.assertEqual({**before.options,**OPTIONS},result['data'])
        self.assertEqual(before,flow.config_entry)
        flow.hass.config_entries.async_update_entry.assert_not_called()
        flow.hass.config_entries.async_reload.assert_not_awaited()

    async def test_disable_restores_original_options_and_rejects_invalid_enabled_values(self):
        scope=load_flows();scope['vol']=vol
        flow=options_flow(scope)
        flow.config_entry.options={**OPTIONS,'entity_name_language':'de'}
        result=await flow.async_step_power_flow_settings({'power_flow_hysteresis':False})
        self.assertEqual({'entity_name_language':'de'},result['data'])
        for on,off in ((10,10),(10,50),(-1,0),('NaN',0),(50,-1),(1000001,0),(True,0)):
            result=await flow.async_step_power_flow_settings({**OPTIONS,'power_flow_on_w':on,'power_flow_off_w':off})
            self.assertEqual({'base':'invalid_power_flow_thresholds'},result['errors'])
            self.assertEqual({**OPTIONS,'entity_name_language':'de'},flow.config_entry.options)
