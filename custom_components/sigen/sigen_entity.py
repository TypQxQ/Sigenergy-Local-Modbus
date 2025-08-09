"""Base entity for Sigenergy ESS integration."""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity  # pylint: disable=syntax-error

from .const import (
    DOMAIN,
    DEVICE_TYPE_PLANT,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
)
from .coordinator import SigenergyDataUpdateCoordinator
from .common import generate_unique_entity_id, generate_device_id

_LOGGER = logging.getLogger(__name__)


def _generate_device_info(
    device_type: str,
    device_name: str,
    coordinator: SigenergyDataUpdateCoordinator,
) -> DeviceInfo:
    """Generate device information for a Sigenergy entity."""
    config_entry_id = coordinator.hub.config_entry.entry_id
    plant_device_identifier = (DOMAIN, f"{config_entry_id}_plant")

    if device_type == DEVICE_TYPE_PLANT:
        return DeviceInfo(
            identifiers={plant_device_identifier},
            name=device_name,
            manufacturer="Sigenergy",
            model="Energy Storage System",
        )

    device_info_data = {
        "identifiers": {(DOMAIN, f"{config_entry_id}_{generate_device_id(device_name)}")},
        "name": device_name,
        "manufacturer": "Sigenergy",
        "via_device": plant_device_identifier,
    }

    if device_type == DEVICE_TYPE_INVERTER:
        inverter_data = (coordinator.data or {}).get("inverters", {}).get(device_name, {})
        device_info_data.update(
            {
                "model": inverter_data.get("inverter_model_type", "Sigen Inverter"),
                "serial_number": inverter_data.get("inverter_serial_number"),
                "sw_version": inverter_data.get("inverter_machine_firmware_version"),
            }
        )
    elif device_type == DEVICE_TYPE_AC_CHARGER:
        device_info_data["model"] = "AC Charger"
    elif device_type == DEVICE_TYPE_DC_CHARGER:
        device_info_data["model"] = "DC Charger"
    else:
        _LOGGER.warning("Unknown device type '%s' for device '%s'", device_type, device_name)
        device_info_data["model"] = "Unknown Device"

    return DeviceInfo(**device_info_data)


class SigenergyEntity(CoordinatorEntity):
    """Base representation of a Sigenergy entity."""

    _attr_has_entity_name = True  # Use default HA entity naming

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: Any,  # Use Any for broader compatibility initially
        name: str,
        device_type: str,
        device_id: Optional[str] = None,  # Changed from int to str for consistency
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self.hub = coordinator.hub
        # Note: _attr_name is handled by _attr_has_entity_name = True and description.name
        # self._attr_name = name # We might not need this if using has_entity_name=True
        self._device_type = device_type
        self._device_id = device_id  # Store original ID (e.g., slave ID for AC charger)
        self._device_name = device_name  # Store device name (e.g., "Inverter 1", "Plant", "AC Charger 1")
        self._pv_string_idx = pv_string_idx
        self._device_info_override = device_info

        # Set unique ID
        self._attr_unique_id = generate_unique_entity_id(
            device_type, device_name, coordinator, description.key, pv_string_idx
        )

        # Set device info
        if device_info:
            self._attr_device_info = device_info
        else:
            self._attr_device_info = _generate_device_info(
                device_type, device_name, coordinator
            )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success or self.coordinator.data is None:
            return False

        data = self.coordinator.data
        if self._device_type == DEVICE_TYPE_PLANT:
            return "plant" in data
        if self._device_type == DEVICE_TYPE_INVERTER:
            return self._device_name in data.get("inverters", {})
        if self._device_type == DEVICE_TYPE_AC_CHARGER:
            return self._device_name in data.get("ac_chargers", {})
        if self._device_type == DEVICE_TYPE_DC_CHARGER:
            parent_inverter_name = self._device_name.replace(" DC Charger", "").strip()
            return parent_inverter_name in data.get("inverters", {})

        return True