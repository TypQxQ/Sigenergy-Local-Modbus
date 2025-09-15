"""Binary sensor platform for Sigenergy ESS integration."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry # pylint: disable=syntax-error
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DEVICE_TYPE_PLANT
from .coordinator import SigenergyDataUpdateCoordinator
from .sigen_entity import SigenergyEntity
from .common import generate_sigen_entity, safe_decimal
from .calculated_sensor import SigenergyCalculations

_LOGGER = logging.getLogger(__name__)

# Fallback definition in case the previous subtask failed
@dataclass(kw_only=True, frozen=True)
class SigenergyBinarySensorEntityDescription(
    BinarySensorEntityDescription, EntityDescription
):
    """Describes Sigenergy binary sensor entity."""
    # Function to calculate the state based on coordinator data
    value_fn: Optional[Callable[[dict[str, Any]], bool | None]] = None
    # Key of the source sensor in the coordinator data dictionary
    source_key: Optional[str] = None

# Define the calculated binary sensors
PLANT_BINARY_SENSORS: list[SigenergyBinarySensorEntityDescription] = [
    SigenergyBinarySensorEntityDescription(
        key="plant_pv_generating",
        name="PV Generating",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:solar-power",
        source_key=None,  # No longer a direct key, calculated from multiple values
        value_fn=lambda data: (
            (
                power := SigenergyCalculations.calculate_total_pv_power(
                    None, coordinator_data={"plant": data}
                )
            )
            is not None
            and power > 0.01
        ),
    ),
    SigenergyBinarySensorEntityDescription(
        key="plant_battery_charging",
        name="Battery Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        icon="mdi:battery-positive", # Standard HA icon
        source_key="plant_ess_power",
        value_fn=lambda data: (dec_val := safe_decimal(data.get("plant_ess_power"))) is not None and dec_val > Decimal("0.01"),
    ),
    SigenergyBinarySensorEntityDescription(
        key="plant_battery_discharging",
        name="Battery Discharging",
        # Using POWER class as BATTERY_CHARGING implies charging=True when ON
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:battery-negative", # Standard HA icon
        source_key="plant_ess_power",
        value_fn=lambda data: (dec_val := safe_decimal(data.get("plant_ess_power"))) is not None and dec_val < Decimal("-0.01"),
    ),
    SigenergyBinarySensorEntityDescription(
        key="plant_exporting_to_grid",
        name="Exporting to Grid",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:transmission-tower-export",
        source_key="plant_grid_sensor_active_power",
        # Exporting is when grid power is positive (Sigenergy convention)
        value_fn=lambda data: (dec_val := safe_decimal(data.get("plant_grid_sensor_active_power"))) is not None and dec_val < Decimal("-0.01"),
    ),
    SigenergyBinarySensorEntityDescription(
        key="plant_importing_from_grid",
        name="Importing from Grid",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:transmission-tower-import",
        source_key="plant_grid_sensor_active_power",
        # Importing is when grid power is negative (Sigenergy convention)
        value_fn=lambda data: (dec_val := safe_decimal(data.get("plant_grid_sensor_active_power"))) is not None and dec_val > Decimal("0.01"),
    ),
]

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy binary sensor platform."""
    coordinator: SigenergyDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    plant_name = config_entry.data[CONF_NAME]

    try:
        entities_to_add = generate_sigen_entity(
            plant_name=plant_name,
            device_name=None,
            device_conn=None,
            coordinator=coordinator,
            entity_class=SigenergyBinarySensor,
            entity_description=PLANT_BINARY_SENSORS,
            device_type=DEVICE_TYPE_PLANT,
            hass=hass
        )

        if entities_to_add:
            async_add_entities(entities_to_add)
            _LOGGER.debug("Added %d plant binary sensors", len(entities_to_add))
    except Exception as ex:
        _LOGGER.exception("Error setting up Sigenergy binary sensors: %s", ex)


class SigenergyBinarySensor(SigenergyEntity, BinarySensorEntity):
    """Representation of a Sigenergy calculated binary sensor."""

    entity_description: SigenergyBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SigenergyBinarySensorEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        source_entity_id: str = "",
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator=coordinator,
            description=description,
            name=name,
            device_type=device_type,
            device_id=device_id,
            device_name=device_name,
            device_info=device_info,
            pv_string_idx=pv_string_idx,
        )
        self._source_entity_id = source_entity_id

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.data is None or "plant" not in self.coordinator.data:
            return None

        plant_data = self.coordinator.data["plant"]

        if self.entity_description.value_fn is None:
            _LOGGER.error("[%s] value_fn is not defined", self.entity_id)
            return None

        try:
            return self.entity_description.value_fn(plant_data)
        except (InvalidOperation, TypeError, ValueError) as ex:
            _LOGGER.warning(
                "[%s] Could not calculate state for key '%s': %s",
                self.entity_id,
                self.entity_description.source_key,
                ex,
            )
            return None
        except Exception as ex:
            _LOGGER.exception(
                "[%s] Unexpected error calculating state: %s", self.entity_id, ex
            )
            return None
