"""Diagnostics support for Sigenergy ESS."""
from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry, async_get as async_get_device_registry

try:
    from pymodbus import __version__ as pymodbus_version
except ImportError:
    pymodbus_version = "unknown"

from .const import DOMAIN

TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, "inverter_serial_number", "serial_number", "macaddress"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> Dict[str, Any]:
    """Return diagnostics for a config entry including all devices."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]

    # Get all devices for this config entry
    device_registry = async_get_device_registry(hass)
    devices = device_registry.devices.get_devices_for_config_entry_id(entry.entry_id)
    
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
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "last_exception": str(coordinator.last_exception) \
                if coordinator.last_exception else None,
            "latest_fetch_time": coordinator.latest_fetch_time,
            "largest_update_interval": coordinator.largest_update_interval,
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
        "library_versions": {
            "pymodbus": pymodbus_version,
        },
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
        "modbus_clients": {
            "active_connections": len(hub._clients),
            "connected_clients": len([k for k, v in hub._connected.items() if v]),
            "client_locks": len(hub._locks),
        },
        "library_versions": {
            "pymodbus": pymodbus_version,
        }
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
        all_integration_devices = device_registry.devices.get_devices_for_config_entry_id(entry.entry_id)
        
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
                
        # Add all register intervals information
        device_diagnostics["all_register_intervals"] = {
            "plant": {str(k): v for k, v in hub.plant_register_intervals.items()},
            "inverters": {
                inv_name: {str(k): v for k, v in intervals.items()} 
                for inv_name, intervals in hub.inverter_register_intervals.items()
            },
            "ac_chargers": {
                ac_name: {str(k): v for k, v in intervals.items()} 
                for ac_name, intervals in hub.ac_charger_register_intervals.items()
            },
            "dc_chargers": {
                dc_name: {str(k): v for k, v in intervals.items()} 
                for dc_name, intervals in hub.dc_charger_register_intervals.items()
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