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

**Charger Output Current** uses the smaller of the charger's reported **Rated
Current** and **Input Breaker Rated Current**, instead of a fixed 32 A ceiling.
For example, ratings of 48 A and 60 A permit up to 48 A; 48 A and 40 A permit up
to 40 A. Missing or invalid ratings make the control unavailable until valid
readings return. The diagnostic entities can remain disabled: their underlying
registers are already read by the integration. Commission wiring and breaker
settings through the installer; this control does not change those ratings.

For an address change on the same inverter or AC charger, edit the existing
device through **Configure → Device settings**. B4 reloads with the new address
without deleting its registry entries, preserving IDs, custom names, areas and
entity settings. Do not remove/re-add a device merely to change its IP or port.
An actual hardware replacement requires checking its identity and commissioned
settings; changing an address does not establish that replacement hardware is
the same physical device or that its counters continue the old history.

Explicit removal targets the selected device's integration identifier and its
registry descendants, including its PV/DC children. Similar display names do not
select other inverters. Entries owned by other integrations are left alone.

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

See the [Energy Dashboard setup guide](ENERGY_DASHBOARD.md) for plant and
inverter sensor mappings, the Now view, and the inverter power balance example.

### Third-party PV

See [third-party solar setup](ENERGY_DASHBOARD.md#third-party-pv) for the
matching energy and power entities.

## Power flow indicators

**Configure → Power flow indicators** optionally enables hysteresis for the
existing PV-generating, importing/exporting and battery-charging/discharging
binary sensors. It is off by default, retaining the existing 10 W threshold.

With hysteresis enabled, the defaults turn an indicator on **above 50 W** and
off **at or below 10 W** in its indicated direction. Between those thresholds,
the previous state is retained. For example, import power of 60 → 30 → 5 W gives
on → on → off. The thresholds are shared across these indicators and must
satisfy `0 ≤ off < on ≤ 1000000 W`.

Missing/invalid readings clear the remembered state. After a reload or data
loss, a reading inside the band does not restore an old on-state; the upper
threshold must be exceeded again. Saving changed options reloads the integration.
Disabling the feature restores the normal threshold behavior. Numeric power,
energy counters, polling and control writes are unaffected.

## Grid outage and recovery notifications

In 2.0, use the stored keys `on_grid`, `off_grid_auto` and `off_grid_manual`,
not translated labels. This example can be pasted into an automation's YAML
editor after replacing the entity ID. It creates a local persistent notification;
replace that action with your phone notification if desired.

```yaml
alias: Sigenergy grid connection notifications
triggers:
  - trigger: state
    entity_id: sensor.sigen_plant_grid_connection_status
    from: "on_grid"
    to:
      - "off_grid_auto"
      - "off_grid_manual"
    for: "00:00:30"
    id: outage
  - trigger: state
    entity_id: sensor.sigen_plant_grid_connection_status
    from:
      - "off_grid_auto"
      - "off_grid_manual"
    to: "on_grid"
    for: "00:00:30"
    id: restored
actions:
  - action: persistent_notification.create
    data:
      notification_id: sigenergy_grid_connection
      title: Sigenergy grid connection
      message: >-
        {{ 'Grid connection restored.' if trigger.id == 'restored'
           else 'The system is off grid.' }}
mode: queued
max: 2
```

An entity becoming `unavailable` is a communication/data issue, not proof of a
grid outage. Requiring a known previous grid state avoids false outage/recovery
messages on startup or after data loss. This also means that starting HA while
already off grid produces no new outage notification from this example. Check
the current state separately if that is required. Manual off-grid operation is
not necessarily a utility failure. The 30-second timer resets on an HA restart
or automation reload; see [HA state triggers](https://www.home-assistant.io/docs/automation/trigger/#state-trigger).

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
