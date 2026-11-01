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

The native unit is **hours**. Existing dashboard templates such as
`sensor.available_battery_minutes` and user-created helpers are retained. When
switching a dashboard or automation to the new sensor, check its entity ID and
unit; convert hours to minutes where the consumer expects minutes. Historical
helper data is not rewritten or transferred to the new entity.

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
