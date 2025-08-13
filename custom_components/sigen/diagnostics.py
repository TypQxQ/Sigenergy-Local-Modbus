"""Diagnostics support for Sigenergy ESS."""
from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN

TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, "inverter_serial_number", "serial_number", "macaddress"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> Dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]

    diagnostics_data = {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
        },
        "data": async_redact_data(coordinator.data, TO_REDACT),
        "hub_info": {
            "host": "redacted",
            "port": hub._plant_port,
            "plant_id": hub.plant_id,
            "inverter_count": hub.inverter_count,
            "ac_charger_count": hub.ac_charger_count,
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
            device_type = "inverter"
            device_name = device_id.replace("_", " ").title()
        elif "ac_charger" in device_id.lower():
            device_type = "ac_charger"  
            device_name = device_id.replace("_", " ").title()
        elif "dc_charger" in device_id.lower():
            device_type = "dc_charger"
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
        },
        "hub_connection": {
            "host": "redacted",
            "port": hub._plant_port,
            "plant_id": hub.plant_id,
            "inverter_count": hub.inverter_count,
            "ac_charger_count": hub.ac_charger_count,
            "read_only": hub.read_only,
        }
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
            device_diagnostics["available_inverters"] = list(coordinator.data.get("inverters", {}).keys())
        elif device_type == "ac_charger":
            # Find AC charger data
            ac_charger_data = {}
            for ac_name, ac_data in coordinator.data.get("ac_chargers", {}).items():
                if device_name.lower().replace(" ", "_") in ac_name.lower().replace(" ", "_"):
                    ac_charger_data = ac_data
                    break
            device_diagnostics["device_data"] = async_redact_data(ac_charger_data, TO_REDACT)
            device_diagnostics["available_ac_chargers"] = list(coordinator.data.get("ac_chargers", {}).keys())
        elif device_type == "dc_charger":
            # Find DC charger data  
            dc_charger_data = {}
            for dc_name, dc_data in coordinator.data.get("dc_chargers", {}).items():
                if device_name.lower().replace(" ", "_") in dc_name.lower().replace(" ", "_"):
                    dc_charger_data = dc_data
                    break
            device_diagnostics["device_data"] = async_redact_data(dc_charger_data, TO_REDACT)
            device_diagnostics["available_dc_chargers"] = list(coordinator.data.get("dc_chargers", {}).keys())
    else:
        device_diagnostics["device_data"] = {}
        device_diagnostics["error"] = "No coordinator data available"
    
    # Apply redaction to the entire device diagnostics to catch any MAC addresses
    return async_redact_data(device_diagnostics, TO_REDACT)