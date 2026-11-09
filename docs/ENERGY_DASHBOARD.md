# Configure the Home Assistant Energy Dashboard

Open **Settings → Dashboards → Energy**. Select the actual entity IDs from your
installation. The tables use English display names and stable integration keys
to help distinguish similarly named entities. Enable any required disabled
entities and keep their Recorder statistics.

## Grid and battery

| Dashboard field | Plant entity |
| --- | --- |
| Grid consumption | **Total imported energy** (`plant_accumulated_grid_import_energy`) |
| Return to grid | **Total exported energy** (`plant_accumulated_grid_export_energy`) |
| Grid power in Now | **Grid active power** (`plant_grid_sensor_active_power`), **Standard** |
| Energy into battery | **Total charged energy of the ESS** (`plant_accumulated_battery_charge_energy`) |
| Energy out of battery | **Total discharged energy of the ESS** (`plant_accumulated_battery_discharge_energy`) |
| Battery power in Now | **Battery power** (`plant_ess_power`), **Inverted** |

Grid power is positive during import and negative during export. Set the signed
grid power once. **Consumed power** measures household load. It belongs outside
the grid connection settings. Sigenergy battery power is positive while charging.
**Inverted** converts it to the direction Home Assistant expects.

Use lifetime energy counters in kWh for the dashboard. Home Assistant calculates
the change across the selected period. Daily counters reset at the device's day
boundary and are useful on their own cards. They are also redundant if you already
track the lifetime counter for that flow.

## Choose a solar setup

Keep each solar entry's energy and power sensors on the same measurement boundary.

| Installation | Solar energy | Solar power in Now |
| --- | --- | --- |
| One Sigenergy inverter, plant view | Plant **Total PV generation** (`plant_accumulated_pv_energy`) | Plant **Sigen PV power** (`plant_sigen_photovoltaic_power`) |
| Multiple Sigenergy inverters, plant view | One plant **Total PV generation** entry | One plant **Sigen PV power** entry |
| One or more Sigenergy inverters, inverter view | One **Total PV energy** (`inverter_accumulated_pv_energy`) entry per inverter | That same inverter's **PV power** (`inverter_pv_power`) |
| Sigenergy plus third-party solar | Use one entry per nonoverlapping source, as described below | Use each entry's matching PV power |

The plant total is convenient for a system-wide view. An inverter counter is
useful for showing each inverter separately or when the plant counter is absent.
For the same panels, select one of these views to avoid double counting.
The combined plant **PV power** (`plant_photovoltaic_power`) includes Sigen and
third-party generation. Use it only with an energy source covering both.

### Third-party PV

If third-party solar is measured separately, enable **Total generation of
third-party inverter** (`plant_total_generation_of_third_party_inverter`) and
pair it with **Third-party PV power** (`plant_third_party_photovoltaic_power`).
An external meter covering the same equipment can supply this pair instead.
If your plant total already includes this generation, use inverter entries for
the Sigenergy portion or use the verified combined total once.

The optional **Daily third-party inverter energy** sensor derives from the
lifetime counter. Enable and record the lifetime source when using it. The
normal **Daily PV energy** sensor sums the Sigenergy inverters' daily counters
without adding the separate third-party daily counter. Home Assistant can
display energy in MWh even though the native counter uses kWh.

## Optional home meter

If the dashboard offers **Home consumption → Add home meter**, and the Sigenergy
load meter covers your whole home, use plant **Total load consumption**
(`plant_accumulated_consumed_energy`) with optional **Total load power**
(`plant_total_load_power`). Confirm both readings include the same loads,
including any charger or circuits outside the gateway. This meter supplies the
home total. Grid, solar and battery still use their own entries. Individual
devices remain a breakdown of home consumption. See the
[Home Assistant home meter documentation](https://www.home-assistant.io/docs/energy/home-consumption/).

## Verify the result

1. In **Now**, check a known period of grid import or export, solar generation,
   and battery charging or discharging. Confirm the direction of each flow.
2. Compare the dashboard's kWh totals over an interval against the change in
   the corresponding lifetime counters.
3. If totals look high, check for overlapping plant and inverter PV totals,
   an incorrect battery power direction, or a load sensor assigned to the grid.
4. If an entity is missing from the selector, check register support, entity
   enablement and [Home Assistant's statistics guidance](https://www.home-assistant.io/docs/energy/faq/#troubleshooting-missing-entities).

## Inverter power balance example

This optional diagnostic estimates the signed difference between PV input,
AC active power and battery charge/discharge power on one hybrid inverter.
Use it during steady operation with the DC charger idle and verify that AC
output reads positive and battery charging reads positive on your installation.
Use the inverter's native aggregate **PV power** (31035), **Active power**
(30587) and **Battery power** (30599). The native PV sensor covers the inverter's
strings without listing them individually.

Replace the three example entity IDs below. Add the sensor under your existing
`template:` configuration. The inputs must all use kW. If an entity reports
W, convert its reading to kW before subtracting.

```yaml
template:
  - sensor:
      - name: Sigen inverter power balance residual
        device_class: power
        state_class: measurement
        unit_of_measurement: kW
        availability: >
          {{ is_number(states('sensor.sigen_inverter_pv_power'))
             and is_number(states('sensor.sigen_inverter_active_power'))
             and is_number(states('sensor.sigen_inverter_battery_power')) }}
        state: >
          {% set pv = states('sensor.sigen_inverter_pv_power') %}
          {% set ac = states('sensor.sigen_inverter_active_power') %}
          {% set battery = states('sensor.sigen_inverter_battery_power') %}
          {% if is_number(pv) and is_number(ac) and is_number(battery) %}
            {{ (pv | float - ac | float - battery | float) | round(3) }}
          {% else %}
            {{ none }}
          {% endif %}
```

Compare several steady samples. The residual can include conversion losses,
internal consumption, differences in measurement points, and changes between
register reads. Keep negative values for diagnosis. The protocol does not
specify the physical measurement point of 30599, so use this as a diagnostic
display outside the Energy Dashboard's consumption inputs. See
[discussion #88](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions/88)
and [the measurement point question](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions/430).
