"""Diagnostics support for Sigenergy ESS."""
from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.components.modbus.connection import async_get_connection_info
from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import (
    DeviceEntry,
    async_entries_for_config_entry,
    async_get as async_get_device_registry,
)

try:
    from pymodbus import __version__ as pymodbus_version
except ImportError:
    pymodbus_version = "unknown"

try:
    from tmodbus import __version__ as tmodbus_version
except ImportError:
    tmodbus_version = "unknown"

from .const import CONF_INVERTER_HAS_DCCHARGER, DOMAIN
from .alarm import unknown_alarm_diagnostics
from .modbusregisterdefinitions import (
    AC_CHARGER_PARAMETER_REGISTERS,
    AC_CHARGER_RUNNING_INFO_REGISTERS,
    DC_CHARGER_PARAMETER_REGISTERS,
    DC_CHARGER_RUNNING_INFO_REGISTERS,
    INVERTER_PARAMETER_REGISTERS,
    INVERTER_RUNNING_INFO_REGISTERS,
    PLANT_ESS_PREHEATING_REGISTERS,
    PLANT_PARAMETER_REGISTERS,
    PLANT_RUNNING_INFO_REGISTERS,
)

TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, "inverter_serial_number", "serial_number", "macaddress"}


def _register_support_summary(
    hub, device_type: str, device_name: Optional[str], register_names: set[str]
) -> Dict[str, Optional[bool]]:
    """Return `{register_name: True/False/None}` for one device's registers."""
    return {
        name: hub.get_register_support(device_type, device_name, name)
        for name in sorted(register_names)
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> Dict[str, Any]:
    """Return diagnostics for a config entry including all devices."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]

    # Get all devices for this config entry
    device_registry = async_get_device_registry(hass)
    devices = async_entries_for_config_entry(device_registry, entry.entry_id)
    
    # Build device diagnostics for all devices
    all_devices_diagnostics = {}
    config_entry_id = entry.entry_id
    
    for device in devices:
        # Extract device information from the device identifiers
        device_identifier = None
        for identifier in device.identifiers:
            if identifier[0] == DOMAIN:
                device_identifier = identifier[1]
                break
        
        if not device_identifier:
            continue
            
        # Parse the device identifier to determine device type and name
        if device_identifier == f"{config_entry_id}_plant":
            device_type = "plant"
            device_name = "plant"
        else:
            # Remove config entry prefix to get the actual device ID
            device_id = device_identifier.replace(f"{config_entry_id}_", "")
            
            # Determine device type based on device ID pattern
            if "inverter" in device_id.lower():
                if "pv" in device_id.lower():
                    device_type = "pv_string"
                    # Extract PV string number
                    pv_num = None
                    for i in range(1, 25):  # Support up to 24 PV strings
                        if f"pv{i}" in device_id.lower():
                            pv_num = i
                            break
                    device_name = device_id.replace("_", " ").title()
                elif "dc_charger" in device_id.lower():
                    device_type = "dc_charger"
                    device_name = device_id.replace("_", " ").title()
                else:
                    device_type = "inverter"
                    device_name = device_id.replace("_", " ").title()
            elif "ac_charger" in device_id.lower():
                device_type = "ac_charger"  
                device_name = device_id.replace("_", " ").title()
            else:
                device_type = "unknown"
                device_name = device_id
        
        # Build device diagnostic data
        device_diagnostics = {
            "device_info": async_redact_data({
                "name": device.name,
                "model": device.model,
                "manufacturer": device.manufacturer,
                "sw_version": device.sw_version,
                "serial_number": device.serial_number,
                "identifiers": list(device.identifiers),
                "connections": list(device.connections) if device.connections else None,
            }, TO_REDACT),
            "device_type": device_type,
            "device_identifier": device_identifier,
        }
        
        # Add device-specific data from coordinator
        if coordinator.data:
            if device_type == "plant":
                device_diagnostics["device_data"] = async_redact_data(coordinator.data.get("plant", {}), TO_REDACT)
            elif device_type == "inverter":
                # Find the inverter data by matching device name patterns
                inverter_data = {}
                for inv_name, inv_data in coordinator.data.get("inverters", {}).items():
                    if device_name.lower().replace(" ", "_") in inv_name.lower().replace(" ", "_"):
                        inverter_data = inv_data
                        break
                device_diagnostics["device_data"] = async_redact_data(inverter_data, TO_REDACT)
            elif device_type == "ac_charger":
                # Find AC charger data
                ac_charger_data = {}
                for ac_name, ac_data in coordinator.data.get("ac_chargers", {}).items():
                    if device_name.lower().replace(" ", "_") in ac_name.lower().replace(" ", "_"):
                        ac_charger_data = ac_data
                        break
                device_diagnostics["device_data"] = async_redact_data(ac_charger_data, TO_REDACT)
            elif device_type == "dc_charger":
                # Find DC charger data  
                dc_charger_data = {}
                for dc_name, dc_data in coordinator.data.get("dc_chargers", {}).items():
                    if device_name.lower().replace(" ", "_") in dc_name.lower().replace(" ", "_"):
                        dc_charger_data = dc_data
                        break
                device_diagnostics["device_data"] = async_redact_data(dc_charger_data, TO_REDACT)
            elif device_type == "pv_string":
                # PV string data is contained within inverter data
                # Extract the parent inverter name and PV string number
                parent_inverter = device_id.split("_pv")[0].replace(f"{config_entry_id}_", "").replace("_", " ").title()
                pv_num = None
                for i in range(1, 25):  # Support up to 24 PV strings
                    if f"pv{i}" in device_id.lower():
                        pv_num = i
                        break
                
                pv_string_data = {}
                for inv_name, inv_data in coordinator.data.get("inverters", {}).items():
                    if parent_inverter.lower().replace(" ", "_") in inv_name.lower().replace(" ", "_"):
                        if pv_num:
                            # Extract PV-specific data from inverter data
                            pv_string_data = {
                                f"pv{pv_num}_voltage": inv_data.get(f"inverter_pv{pv_num}_voltage"),
                                f"pv{pv_num}_current": inv_data.get(f"inverter_pv{pv_num}_current"),
                                "parent_inverter": inv_name,
                                "pv_string_number": pv_num,
                                "pv_string_count": inv_data.get("inverter_pv_string_count"),
                                "mppt_count": inv_data.get("inverter_mppt_count"),
                                "total_pv_power": inv_data.get("inverter_pv_power"),
                            }
                        break
                device_diagnostics["device_data"] = async_redact_data(pv_string_data, TO_REDACT)
                device_diagnostics["parent_inverter"] = parent_inverter
                device_diagnostics["pv_string_number"] = pv_num
        else:
            device_diagnostics["device_data"] = {}
        
        # Use device name as key for the diagnostics
        all_devices_diagnostics[device.name or device_identifier] = device_diagnostics

    diagnostics_data = {
        "unknown_alarm_values": unknown_alarm_diagnostics(coordinator.data),
        "discovery": hub.discovery_diagnostics(),
        "pv_restore": coordinator.pv_restore_diagnostics(),
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "last_exception": str(coordinator.last_exception) \
                if coordinator.last_exception else None,
            "latest_fetch_time": coordinator.latest_fetch_time,
            "largest_update_interval": coordinator.largest_update_interval,
            "startup_fetch_time": coordinator.startup_fetch_time,
            "update_timeout_count": coordinator.update_timeout_count,
            "update_failure_count": coordinator.update_failure_count,
            "partial_update_count": coordinator.partial_update_count,
            "latest_update_timing": coordinator.latest_update_timing,
        },
        "data": async_redact_data(coordinator.data, TO_REDACT),
        "hub_info": {
            "host": "redacted",
            "port": hub._plant_port,
            "plant_id": hub.plant_id,
            "inverter_count": hub.inverter_count,
            "ac_charger_count": hub.ac_charger_count,
            "read_only": hub.read_only,
        },
        "home_assistant_version": HA_VERSION,
        "library_versions": {
            "pymodbus": pymodbus_version,
            "tmodbus": tmodbus_version,
        },
        "modbus_connections": [
            {
                "endpoint": async_redact_data({"host": info.endpoint[1], "port": info.endpoint[-1]}, TO_REDACT),
                "connected": info.connected,
                "config_entries_sharing": len(info.units),
            }
            for info in async_get_connection_info(hass)
        ],
        "all_devices": all_devices_diagnostics,
        "device_summary": {
            "total_devices": len(devices),
            "device_types": {device_type: len([d for d in all_devices_diagnostics.values() if d["device_type"] == device_type])
                           for device_type in set(d["device_type"] for d in all_devices_diagnostics.values())},
        },
    }

    # Apply redaction to the entire diagnostics data to catch any MAC addresses
    return async_redact_data(diagnostics_data, TO_REDACT)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> Dict[str, Any]:
    """Return diagnostics for a device entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]
    
    # Extract device information from the device identifiers
    device_identifier = None
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN:
            device_identifier = identifier[1]
            break
    
    if not device_identifier:
        return {"error": "Device identifier not found"}
    
    # Parse the device identifier to determine device type and name
    config_entry_id = entry.entry_id
    if device_identifier == f"{config_entry_id}_plant":
        device_type = "plant"
        device_name = "plant"
    else:
        # Remove config entry prefix to get the actual device ID
        device_id = device_identifier.replace(f"{config_entry_id}_", "")
        
        # Determine device type based on device ID pattern
        if "inverter" in device_id.lower():
            if "pv" in device_id.lower():
                device_type = "pv_string"
                # Extract PV string number
                pv_num = None
                for i in range(1, 25):  # Support up to 24 PV strings
                    if f"pv{i}" in device_id.lower():
                        pv_num = i
                        break
                device_name = device_id.replace("_", " ").title()
            elif "dc_charger" in device_id.lower():
                device_type = "dc_charger"
                device_name = device_id.replace("_", " ").title()
            else:
                device_type = "inverter"
                device_name = device_id.replace("_", " ").title()
        elif "ac_charger" in device_id.lower():
            device_type = "ac_charger"  
            device_name = device_id.replace("_", " ").title()
        else:
            device_type = "unknown"
            device_name = device_id
    
    # Base diagnostic data for the device
    device_diagnostics = {
        "unknown_alarm_values": unknown_alarm_diagnostics(coordinator.data),
        "discovery": hub.discovery_diagnostics(),
        "pv_restore": coordinator.pv_restore_diagnostics(),
        "device_info": async_redact_data({
            "name": device.name,
            "model": device.model,
            "manufacturer": device.manufacturer,
            "sw_version": device.sw_version,
            "serial_number": device.serial_number,
            "identifiers": list(device.identifiers),
            "connections": list(device.connections) if device.connections else None,
        }, TO_REDACT),
        "device_type": device_type,
        "device_identifier": device_identifier,
        "coordinator_status": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
            "latest_fetch_time": coordinator.latest_fetch_time,
            "largest_update_interval": coordinator.largest_update_interval,
            "startup_fetch_time": coordinator.startup_fetch_time,
            "update_timeout_count": coordinator.update_timeout_count,
            "update_failure_count": coordinator.update_failure_count,
            "partial_update_count": coordinator.partial_update_count,
            "latest_update_timing": coordinator.latest_update_timing,
            "sensors_initialized": coordinator.data.get("_sensors_initialized", False) if coordinator.data else False,
        },
        "hub_connection": {
            "host": "redacted",
            "port": hub._plant_port,
            "plant_id": hub.plant_id,
            "inverter_count": hub.inverter_count,
            "ac_charger_count": hub.ac_charger_count,
            "read_only": hub.read_only,
            "probe_retry_delay": hub.probe_retry_delay,
        },
        "modbus_connections": [
            {
                "endpoint": async_redact_data({"host": info.endpoint[1], "port": info.endpoint[-1]}, TO_REDACT),
                "connected": info.connected,
                "config_entries_sharing": len(info.units),
            }
            for info in async_get_connection_info(hass)
        ],
        "home_assistant_version": HA_VERSION,
        "library_versions": {
            "pymodbus": pymodbus_version,
            "tmodbus": tmodbus_version,
        },
    }
    
    # Add device-specific data from coordinator
    if coordinator.data:
        # Extract PV string data from inverter data for comprehensive diagnostics
        pv_strings_data = {}
        for inv_name, inv_data in coordinator.data.get("inverters", {}).items():
            pv_string_count = inv_data.get("inverter_pv_string_count", 0)
            if pv_string_count and pv_string_count > 0:
                for pv_num in range(1, int(pv_string_count) + 1):
                    pv_voltage = inv_data.get(f"inverter_pv{pv_num}_voltage")
                    pv_current = inv_data.get(f"inverter_pv{pv_num}_current")
                    
                    # Only include PV strings with data
                    if pv_voltage is not None or pv_current is not None:
                        pv_string_key = f"{inv_name} PV{pv_num}"
                        pv_strings_data[pv_string_key] = {
                            f"pv{pv_num}_voltage": pv_voltage,
                            f"pv{pv_num}_current": pv_current,
                            "parent_inverter": inv_name,
                            "pv_string_number": pv_num,
                            "pv_string_count": inv_data.get("inverter_pv_string_count"),
                            "mppt_count": inv_data.get("inverter_mppt_count"),
                            "total_pv_power": inv_data.get("inverter_pv_power"),
                            "daily_pv_energy": inv_data.get("inverter_daily_pv_energy"),
                            "accumulated_pv_energy": inv_data.get("inverter_accumulated_pv_energy"),
                        }
        
        # Always include all device data for comprehensive diagnostics
        device_diagnostics["all_device_data"] = {
            "plant": async_redact_data(coordinator.data.get("plant", {}), TO_REDACT),
            "inverters": async_redact_data(coordinator.data.get("inverters", {}), TO_REDACT),
            "pv_strings": async_redact_data(pv_strings_data, TO_REDACT),
            "ac_chargers": async_redact_data(coordinator.data.get("ac_chargers", {}), TO_REDACT),
            "dc_chargers": async_redact_data(coordinator.data.get("dc_chargers", {}), TO_REDACT),
        }

        # Add comprehensive device registry information for all devices in this integration
        device_registry = async_get_device_registry(hass)
        all_integration_devices = async_entries_for_config_entry(
            device_registry, entry.entry_id
        )
        
        device_diagnostics["all_integration_devices"] = {}
        for dev in all_integration_devices:
            dev_identifier = None
            for identifier in dev.identifiers:
                if identifier[0] == DOMAIN:
                    dev_identifier = identifier[1]
                    break
            if dev_identifier:
                device_diagnostics["all_integration_devices"][dev.name or dev_identifier] = {
                    "device_info": async_redact_data({
                        "name": dev.name,
                        "model": dev.model,
                        "manufacturer": dev.manufacturer,
                        "sw_version": dev.sw_version,
                        "serial_number": dev.serial_number,
                        "identifiers": list(dev.identifiers),
                        "connections": list(dev.connections) if dev.connections else None,
                    }, TO_REDACT),
                    "device_identifier": dev_identifier,
                }
                
        # Add register support information: True/False once probed, None while
        # still unresolved (see sigen_devices.py's register-support probing).
        device_diagnostics["all_register_support"] = {
            "plant": _register_support_summary(
                hub,
                "plant",
                None,
                set(PLANT_RUNNING_INFO_REGISTERS)
                | set(PLANT_PARAMETER_REGISTERS)
                | set(PLANT_ESS_PREHEATING_REGISTERS),
            ),
            "inverters": {
                inv_name: _register_support_summary(
                    hub,
                    "inverter",
                    inv_name,
                    set(INVERTER_RUNNING_INFO_REGISTERS) | set(INVERTER_PARAMETER_REGISTERS),
                )
                for inv_name in hub.inverter_connections
            },
            "ac_chargers": {
                ac_name: _register_support_summary(
                    hub,
                    "ac_charger",
                    ac_name,
                    set(AC_CHARGER_RUNNING_INFO_REGISTERS) | set(AC_CHARGER_PARAMETER_REGISTERS),
                )
                for ac_name in hub.ac_charger_connections
            },
            "dc_chargers": {
                inv_name: _register_support_summary(
                    hub,
                    "dc_charger",
                    f"{inv_name} DC Charger",
                    set(DC_CHARGER_RUNNING_INFO_REGISTERS) | set(DC_CHARGER_PARAMETER_REGISTERS),
                )
                for inv_name, info in hub.inverter_connections.items()
                if info.get(CONF_INVERTER_HAS_DCCHARGER, False)
            },
        }
        
        # Add all connection information
        device_diagnostics["all_connections"] = {
            "plant": async_redact_data({
                "host": hub._plant_host,
                "port": hub._plant_port,
                "slave_id": hub.plant_id,
            }, TO_REDACT),
            "inverters": async_redact_data(hub.inverter_connections, TO_REDACT),
            "ac_chargers": async_redact_data(hub.ac_charger_connections, TO_REDACT),
        }
    else:
        device_diagnostics["device_data"] = {}
        device_diagnostics["error"] = "No coordinator data available"
    
    # Apply redaction to the entire device diagnostics to catch any MAC addresses
    return async_redact_data(device_diagnostics, TO_REDACT)
