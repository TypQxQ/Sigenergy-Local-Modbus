## Sigenergy Local Modbus (sigen) – AI Coding Notes

- Integration lives in custom_components/sigen/ and follows ConfigEntry + DataUpdateCoordinator.
- Core flow: config_flow.py → __init__.py (setup entry) → modbus.py (SigenergyModbusHub) → coordinator.py → platform entities.

### Domain model
- A “Plant” represents one Modbus TCP endpoint (host/port). Current config flow assumes one plant per HA instance.
- Plant default slave id is 247; device ids are 1–246 for inverters/chargers.
- DC charger data is read “via inverter” (see config_flow.py + sensor.py/switch.py device_info via_device relationships).

### Modbus + polling
- Keep all I/O async; avoid adding new blocking calls (especially inside coordinator updates).
- Connections come from Home Assistant's shared Modbus broker (homeassistant.components.modbus): async_get_unit() in modbus.py, async_get_temporary_unit() in config_flow.py. Do not open sockets or import pymodbus directly; the broker owns connection sharing, locking and reconnects.
- Register layouts are modbus_connection Components built in sigen_components.py from the tables in modbusregisterdefinitions.py; add or change registers in those tables, not by hand-writing fields.
- sigen_devices.py probes register support per field and reads supported fields as strictly contiguous blocks (max ~123 registers/read), with each block's failure isolated from the others.
- Wrap every wire-touching await in asyncio.shield(): cancelling an in-flight request (e.g. an automation restarting mid-write) orphans its response on the shared connection and forces a reconnect.

### Entities + naming
- Use SigenergyEntity (sigen_entity.py) as the base and create entities through generate_sigen_entity() (common.py) like existing platform files do.
- Use device_name (not numeric id) as the primary inverter identifier across coordinator/entity layers.
- PV strings are modeled as sub-devices under the inverter; follow sensor.py’s PV device_info pattern.

### Writes / controls safety
- All writes must go through SigenergyDataUpdateCoordinator.async_write_parameter() (coordinator.py); do not call Modbus write methods directly from entities.
- Respect read-only behavior (hub.read_only) and keep “dangerous” controls disabled by default (entity_registry_enabled_default=False).

### Calculated sensors
- Calculated/integration sensors are in calculated_sensor.py; they may use Recorder history via executor jobs—keep history work off the event loop.
- If you add new calculations, prefer pure functions in SigenergyCalculations and reuse safe_decimal/safe_float (common.py).

### Diagnostics + privacy
- diagnostics.py must redact host/credentials/serials/MACs via async_redact_data; never log secrets.

### CI / repo checks
- This repo is validated by hassfest and HACS Action workflows (.github/workflows/).
- Type checking uses pyright “basic” (pyrightconfig.json); Reference/ is intentionally excluded.
