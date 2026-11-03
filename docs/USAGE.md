# Devices and controls

For installation and setup, see the [README](../README.md). For the 2.0 status
sensor changes and localization, see the [migration guide](migrations/2.0.md).

## Device layout

A plant groups devices on one Modbus TCP endpoint, normally the first inverter's
IP address with port 502. The plant uses device ID 247; inverters and AC chargers
have their own IDs. An AC charger normally uses ID 1, so use another ID for the
inverter when both share the endpoint. DC charger data is read through its
inverter, rather than through an independent charger connection.

Plant sensors expose power flows, energy totals, battery state of charge and EMS
status. Inverter sensors add PV string, battery and phase data; chargers expose
charging measurements and status. Available fields depend on device and firmware.

Use **Settings → Devices & services → Add integration → Sigenergy ESS** to add
devices to an existing plant. Use the integration entry's **Configure** menu to
change plant or device settings. Keep the same host and port across a plant
unless your installation requires a different layout.

Each inverter or AC charger needs a unique combination of host, port and device
ID. B4 rejects an address already assigned to another device before probing or
saving it, including when editing a device. The same ID can be used on different
hosts or ports. A DC charger shares its parent inverter's address. Hostname
matching ignores case and a trailing dot; DNS aliases are not resolved.

If an AC charger cannot be found, confirm its commissioned device ID with the
installer. The charger normally uses the inverter's Modbus TCP endpoint, not its
own Wi-Fi IP. A plant-level EVAC energy reading does not prove that the charger's
own registers are reachable. See the resolved [replacement charger report](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions/391).

## Model and firmware notes

Firmware numbering is model-specific; an SPC number is not a universal minimum
across all Sigenergy products.

| Model | Evidence and limits |
|---|---|
| SigenStor | SPC109+ is the usual baseline for the Modbus functionality used here; available registers still depend on firmware and equipment. |
| SigenMate | A user reports working read-only operation on **V100R001C00SPC101** after enabling Modbus in the app. Sigenergy supplied protocol V2.9 to that user. This is a field report, not verification of every plant/inverter/PV sensor or any write controls. |

The [SigenMate report and vendor response](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions/395)
supersede the earlier blanket statement in that thread that SigenMate could not
connect. Confirm the exact model, full firmware string, Modbus setting and
applicable protocol when reporting compatibility. Start with read-only mode.

## Home Assistant Energy Dashboard

Use **Settings → Dashboards → Energy** to select your actual entity IDs. The
labels below identify integration entities; IDs can differ between installations.
Enable required disabled sensors in entity settings and retain their Recorder
history. Check for statistics errors if a sensor is missing from the selector.

For accumulated **energy** (kWh), use one source for each flow:

| Energy setting | Integration sensor |
|---|---|
| Grid import | Plant **Total imported energy** (`plant_accumulated_grid_import_energy`) |
| Grid export | Plant **Total exported energy** (`plant_accumulated_grid_export_energy`) |
| Solar production | Plant **Total PV generation** (`plant_accumulated_pv_energy`) |
| Energy into battery | Plant **Total charged energy of the ESS** (`plant_accumulated_battery_charge_energy`) |
| Energy out of battery | Plant **Total discharged energy of the ESS** (`plant_accumulated_battery_discharge_energy`) |

For the **Now** view, configure **power** measurements (kW):

| Power setting | Integration sensor and sign |
|---|---|
| Grid connection | Plant **Grid active power** (`plant_grid_sensor_active_power`): positive import, negative export; use Standard. |
| Solar | Plant **PV power** (`plant_photovoltaic_power`): non-negative generation. |
| Battery | Plant **Battery power** (`plant_ess_power`): positive charging, negative discharging; use Inverted to match HA's positive-discharge convention. |

Do not assign **Consumed power** to the grid connection: it measures household
consumption, not grid import/export. Use the signed grid sensor once, rather
than adding its split import/export power sensors as additional grid sources.
Likewise, choose either the plant's PV total or individual PV energy sensors;
adding both counts the same generation twice. Third-party PV or separate meters
may require a different topology; confirm what each meter includes first.

After setup, check direction during a known import, export, charge and discharge
period, and check that kWh totals and the kW Now view agree with those directions.
No manual template or history migration is needed for this mapping. See Home
Assistant's [energy guidance](https://www.home-assistant.io/docs/energy/faq/).

## Recorder storage

Disable entities you do not use in entity settings. For live readings you want
to keep but do not need in history, exclude only those specific entities from
Recorder. For example, merge this into your existing `recorder` block, replacing
the example IDs:

```yaml
recorder:
  exclude:
    entities:
      - sensor.sigen_plant_modbus_fetch_time
      - sensor.sigen_plant_modbus_max_data_fetch_time_since_restart
```

Excluded entities lose future history/statistics. Keep Energy Dashboard sources,
PV accumulated/daily energy integrators and any other sensors needed for restore
or historical calculations recorded. Avoid a blanket exclusion of all `sigen`
entities. Changing `commit_interval` batches writes; it does not reduce the
number of state changes stored. Purging or editing old data is not required.
See [Recorder filtering](https://www.home-assistant.io/integrations/recorder/#configure-filter).

## Battery time remaining

The plant's **Battery time remaining** sensor estimates how long the battery
could supply the current household consumption above the configured backup SOC:

`hours = max(SOC - backup SOC, 0) / 100 × rated capacity (kWh) / consumed power (kW)`

The estimate is available while charging, discharging or idle. It assumes no
further contribution from solar or the grid, and constant household demand.
It uses the same consumption calculation as **Consumed power**, including its
fallback for older firmware. No additional Modbus requests are made.

At or below the reserve it reports `0`. With energy above reserve, zero/negative
or missing consumption reports `unknown`. Missing/invalid SOC, reserve or
capacity also reports `unknown`. This is an estimate, not a guaranteed backup
duration; rated capacity does not account for all losses or battery ageing.

From B4, its attributes show the inputs used in the calculation:

| Attribute | Meaning |
|---|---|
| `battery_soc_percent` | Current battery state of charge (%) |
| `backup_soc_percent` | Configured backup reserve (%) |
| `rated_capacity_kwh` | Rated battery capacity (kWh) |
| `consumed_power_kw` | Current household consumption (kW), using the same calculation as **Consumed power** |
| `usable_energy_kwh` | Energy above the reserve: `max(SOC − backup SOC, 0) / 100 × rated capacity` |

The attributes use the existing cached readings and make no additional Modbus
requests. Missing or invalid inputs are `null`, rather than an assumed zero.
Other valid inputs remain visible when the estimate is unknown. A valid zero
consumption is shown as `0`, while the estimate remains unknown above reserve.
Attribute labels follow the interface language.

The native unit is **hours**. Existing dashboard templates such as
`sensor.available_battery_minutes` and user-created helpers are retained. When
switching a dashboard or automation to the new sensor, check its entity ID and
unit; convert hours to minutes where the consumer expects minutes. Historical
helper data is not rewritten or transferred to the new entity.

Entity IDs in examples are placeholders. A new sensor may instead be named
`sensor.garage_sigen_plant_battery_time_remaining`. Open the sensor's entity
settings and copy its actual entity ID into your template or dashboard. A
reference to a nonexistent ID returns `unknown`. No renaming is required.

## Controls and Remote EMS

Read-only mode is enabled by default. Write controls and many diagnostic entities
are also disabled by default. To use a control, disable read-only mode in the
integration options and enable the relevant entity in Home Assistant.

Changing the plant's Remote EMS enable switch or control mode waits for a new
device readback before the action completes successfully. A matching cached
value or a poll that started before the write is not confirmation. Confirmation
waits for at most 60 seconds and does not repeat the write. Other parameter writes
retain batched updates.

If a readable control's backing value is missing after a failed read, the
control becomes unavailable until that value recovers. Missing readings are
not reported as `0`, `off` or a default option; real zero/off readings remain
valid. Healthy devices continue updating during partial failures. A failed poll
with no usable readings is reported as a coordinator failure. Write-only
commands retain their separate availability rules.

If confirmation fails, the action reports that the write was sent but could not
be confirmed. The inverter may already have applied it: check the current state
before deciding whether another command is needed.

Automations should enable Remote EMS, wait for the enabled state and mode-select
availability, then select the desired mode. A fixed delay is not confirmation.
Home Assistant skips actions targeting unavailable entities before the
integration receives them, so the integration cannot recover an undelivered
command. Include an automation trace and full debug log when reporting failures.

## Alarm monitoring

Use the `alarm_status` attribute when building an alarm indicator. Checking
whether the sensor's state differs from `No Problem` also flags `Unknown Alarm`
and unavailable readings as if they were confirmed faults.

| Reading | Meaning |
|---|---|
| `alarm_status: no_problem` | Valid reading with no alarm bits set |
| `alarm_status: active` | At least one recognized alarm bit is set |
| `alarm_status: unknown` | The reading cannot be fully interpreted |
| Entity `unknown`/`unavailable`, or missing `alarm_status` | No usable alarm status |

For a confirmed-alarm indicator, use this template, replacing the example ID:

```jinja2
{% set alarm = 'sensor.sigen_plant_general_alarm_7' %}
{{ has_value(alarm) and state_attr(alarm, 'alarm_status') == 'active' }}
```

Pair it with a separate uncertainty/offline indicator so a false confirmed-alarm
indicator is not mistaken for an all-clear:

```jinja2
{% set alarm = 'sensor.sigen_plant_general_alarm_7' %}
{{ not has_value(alarm)
   or state_attr(alarm, 'alarm_status') not in ['no_problem', 'active'] }}
```

Alarm 7's `0xFFFF` has `alarm_status: unknown` and
`alarm_reason: all_bits_set`. It activates the uncertainty indicator, not the
confirmed-alarm indicator. The value alone does not establish that the register
is unsupported. Polling continues, so a later valid reading can report a normal
status or a recognized alarm. These attributes use stable keys in every language.

## Modbus limitations

- PCC import/export power-factor controls (40157/40158) accept values greater
  than -1 and at most -0.8, or from 0.8 through 1, as defined in protocol V2.9.
  Values in the middle interval are rejected before a command is sent. At the
  register's 0.001 resolution, the lowest configurable value is -0.999.
- Plant power-limit controls show **Unknown** for all-ones `0xFFFFFFFF`
  readback. This covers ESS charging/discharging, PV, grid import/export and
  PCS import/export limits (40032-40044). The protocol explicitly documents
  this value as invalid for the PCS limits; DEV also reports it for other
  plant limits. These controls remain available for setting a valid limit.
  This value does not prove that the register is unsupported or that the
  limit is unlimited. A communication failure still makes the affected
  control unavailable.
- PSS (Packaged Substation System) and PID (Potential Induced Degradation)
  devices from Modbus protocol V2.9 are excluded because they cannot be tested.
- The Sigen app's grid-side details are not fully exposed through documented
  local Modbus registers. The older grid phase voltage/current sensors were
  removed because they are no longer documented in protocol V2.9.
- Modbus exception code **2** means an illegal data address. The integration
  skips confirmed unsupported registers; a timeout does not prove a register is
  unsupported. Include diagnostics to distinguish discovery from communication
  failures.
- Alarm 7's ambiguous `0xFFFF` is reported as **Unknown Alarm**, rather than
  sixteen confirmed alarms. See the [migration guide](migrations/2.0.md#localized-names-and-alarm-details)
  for translated alarm details and preserved stored states.
