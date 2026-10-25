## Sigenergy Local Modbus (sigen) – AI Coding Notes

- Integration lives in custom_components/sigen/ and follows ConfigEntry + DataUpdateCoordinator.
- Core flow: config_flow.py → __init__.py (setup entry) → modbus.py (SigenergyModbusHub) → coordinator.py → platform entities.

### Domain model
- A “Plant” represents one Modbus TCP endpoint (host/port). Current config flow assumes one plant per HA instance.
- Plant default slave id is 247; device ids are 1–246 for inverters/chargers.
- DC charger data is read “via inverter” (see config_flow.py + sensor.py/switch.py device_info via_device relationships).

### Modbus + polling
- Keep all I/O async; avoid adding new blocking calls (especially inside coordinator updates).
- SigenergyModbusHub in modbus.py caches AsyncModbusTcpClient per (host, port) and guards reads/writes with per-connection asyncio locks.
- Register reads are optimized by probing supported registers and building contiguous read intervals (max ~123 registers/read).
- Maintain pymodbus compatibility via the safe wrapper that handles slave/device_id arg changes.

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
