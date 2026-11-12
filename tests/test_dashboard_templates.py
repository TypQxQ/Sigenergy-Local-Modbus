"""Render the shipped gauge templates through unavailable and recovered inputs."""
import math
from pathlib import Path
import re
import unittest

import jinja2
import yaml

PACKAGE = Path(__file__).resolve().parents[1] / 'Dashboards/energy_flow_gauge/power_flow_gauges_sigen.yaml'


class TestDashboardTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = yaml.safe_load(PACKAGE.read_text(encoding='utf-8'))
        cls.sensors = cls.package['template'][0]['sensor']

    def render(self, text, values):
        def is_number(value):
            try:
                return math.isfinite(float(value))
            except (ValueError, TypeError):
                return False
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.globals.update(states=lambda entity: str(values.get(entity, 'unknown')),
                           is_number=is_number, max=max, min=min)
        return env.from_string(text).render().strip()

    def test_all_templates_preserve_zero_and_recover_after_each_missing_input(self):
        for sensor in self.sensors:
            dependencies = set(re.findall(r"states\('([^']+)'\)", sensor['state']))
            self.assertTrue(dependencies, sensor['name'])
            values = dict.fromkeys(dependencies, '0')
            self.assertEqual('True', self.render(sensor['availability'], values), sensor['name'])
            self.assertEqual(0, float(self.render(sensor['state'], values)))
            for entity in dependencies:
                for missing in ('unknown', 'unavailable', 'NaN', 'Infinity', ''):
                    with self.subTest(sensor=sensor['name'], entity=entity, missing=missing):
                        bad = {**values, entity: missing}
                        self.assertEqual('False', self.render(sensor['availability'], bad))
                        # HA may evaluate state even when availability is false.
                        self.render(sensor['state'], bad)
                self.assertEqual('True', self.render(sensor['availability'], values))

    def test_battery_direction(self):
        names = {s['name']: s for s in self.sensors}
        for power, charging, discharging in ((3,3,0), (-2,0,2), (0,0,0)):
            values = {'sensor.sigen_plant_battery_power': power}
            self.assertEqual(charging, float(self.render(names['Power Flow Gauge - Total Battery Charging']['state'], values)))
            self.assertEqual(discharging, float(self.render(names['Power Flow Gauge - Total Battery Discharging']['state'], values)))

    def test_valid_power_flows_are_not_changed_by_availability_guards(self):
        names = {s['name']: s for s in self.sensors}
        values = {'sensor.power_flow_gauge_total_solar': 6,
                  'sensor.power_flow_gauge_total_consumption': 2,
                  'sensor.power_flow_gauge_total_battery_charging': 3,
                  'sensor.power_flow_gauge_solar_to_house': 2,
                  'sensor.power_flow_gauge_solar_to_battery': 3,
                  'sensor.power_flow_gauge_total_grid_export': 1}
        for name, expected in (('Solar to House', 2), ('Solar to Battery', 3), ('Solar to Grid', 1)):
            sensor = names['Power Flow Gauge - '+name]
            self.assertEqual('True', self.render(sensor['availability'], values))
            self.assertEqual(expected, float(self.render(sensor['state'], values)))
