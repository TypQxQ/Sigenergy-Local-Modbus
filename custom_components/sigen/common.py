"""The Sigenergy ESS integration. Common code."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Callable, Dict
from dataclasses import dataclass
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.sensor import (
    SensorEntityDescription,
)

from .const import (DOMAIN, DEVICE_TYPE_INVERTER, DEVICE_TYPE_DC_CHARGER)

_LOGGER = logging.getLogger(__name__)


def get_suffix_if_not_one(name: str) -> str:
    """Get the last part of the name if it is a number other than 1."""
    return name.split()[-1].strip() + " " if len(name.split()) > 1 and name.split()[-1].isdigit() and name.split()[-1] != "1" else ""

def generate_device_name(plant_name: str, device_name: str) -> str:
    """Generate a device name based on plant name and device name."""
    device_type = " ".join(device_name.split()[:-1]) if len(device_name.split()) > 1 and device_name.split()[-1].isdigit() else device_name
    return f"Sigen {get_suffix_if_not_one(plant_name)}{device_type}{get_suffix_if_not_one(device_name)}"

def generate_sigen_entity(
        plant_name: str,
        device_name: str | None,
        device_conn: dict | None,
        coordinator,
        entity_class: type,
        entity_description: list,
        device_type: str,
        hass: Optional[HomeAssistant] = None,
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
        ) -> list:
    """
    Generate entities for Sigenergy components.
    This function creates a list of entities for a specific device type by
    applying the given entity class with appropriate descriptions.
    Args:
        plant_name (str): Name of the plant/installation
        device_name (str | None): Name of the device, if None will use plant_name
        device_conn (dict | None): Device connection parameters containing slave ID
        coordinator (SigenergyDataUpdateCoordinator): Data update coordinator
        entity_class (type): The entity class to instantiate
        entity_description (list[SigenergyNumberEntityDescription]): List of entity descriptions
        device_type (str): Type of the device
    Returns:
        list: A list of instantiated entities for the device
    """
    device_name = device_name if device_name else plant_name

    entities = []
    for description in entity_description:
        # _LOGGER.debug("Generating entity for description: %s", description.name)

        # Generate PV specific entity names and IDs if applicable
        if pv_string_idx is not None:
            # Add extra parameters for PV string index and device name to the description if needed
            if hasattr(description, "value_fn") and description.value_fn is not None:
                description = SigenergySensorEntityDescription.from_entity_description(
                    description,
                    extra_params={"pv_idx": pv_string_idx, "device_name": device_name},
                )

            pv_string_name = f"{device_name} PV{pv_string_idx}"
            sensor_name = f"{pv_string_name} {description.name}"
            sensor_id = pv_string_name
        elif device_type == DEVICE_TYPE_DC_CHARGER:
            sensor_id = f"{device_name} DC Charger"
            sensor_name = sensor_id
            device_type = DEVICE_TYPE_INVERTER
        else:
            sensor_name = f"{device_name} {description.name}"
            sensor_id = sensor_name

        entity_kwargs = {
            "coordinator": coordinator,
            "description": description,
            "name": sensor_name,
            "device_type": device_type,
            "device_id": generate_device_id(sensor_id, device_type),
            "device_name": device_name,
        }

        if hasattr(description, 'source_key') and description.source_key:
            source_entity_id = get_source_entity_id(
                device_type,
                device_name,
                description.source_key,
                coordinator,
                hass,
                pv_string_idx,
            )
            if source_entity_id:
                entity_kwargs["source_entity_id"] = source_entity_id
            else:
                _LOGGER.warning(
                    "No source entity ID found for source key '%s' (device: %s). Skipping entity '%s'.",
                    description.source_key,
                    device_name,
                    description.name,
                )
                continue  # Skip this entity


        if device_info:
            entity_kwargs["device_info"] = device_info

        if pv_string_idx:
            entity_kwargs["pv_string_idx"] = pv_string_idx

        try:
            new_entity = entity_class(**entity_kwargs)
            entities.append(new_entity)

        except Exception as ex: # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Error creating entity '%s' for device '%s': %s",
                 description.name, device_name, ex) # Use .exception
            _LOGGER.debug(
                "Entity creation failed with description: %s",
                 description)
            _LOGGER.debug(
                "Entity creation failed with kwargs: %s",
                 entity_kwargs)
    return entities

def get_source_entity_id(device_type, device_name, source_key, coordinator, hass, pv_string_idx: Optional[int] = None): # Add pv_string_idx
    """Get the source entity ID for an integration sensor."""
    # Try to find entities by unique ID pattern
    try:
        # Get the Home Assistant entity registry
        ha_entity_registry = async_get_entity_registry(hass)

        # Determine the unique ID pattern to look for
        # If it's a PV string integration sensor, the source key is different
        source_attr_key = source_key
        if pv_string_idx is not None and source_key == "pv_string_power":
            source_attr_key = "power" # The actual source sensor uses 'power' as its key

        unique_id_pattern = generate_unique_entity_id(
            device_type=device_type,
            device_name=device_name,
            coordinator=coordinator,
            attr_key=source_attr_key, # Use the potentially adjusted key
            pv_string_idx=pv_string_idx,
        )

        # _LOGGER.debug("Looking for entity with unique ID pattern: %s", unique_id_pattern)
        entity_id = ha_entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id_pattern)

        if entity_id is None:
            _LOGGER.warning("No entity found for unique ID pattern: %s", unique_id_pattern)
            _LOGGER.debug("unique ID pattern constructed from: \n config_entry_id: %s \n device_type: %s \n device_name: %s \n source_key: %s \n pv_idx: %s",
                            coordinator.hub.config_entry.entry_id, device_type, device_name, source_key, pv_string_idx)
        # else:
        #     _LOGGER.debug("Found entity ID: %s for pattern %s", entity_id, unique_id_pattern)

        return entity_id
    except Exception as ex: # pylint: disable=broad-exception-caught
        _LOGGER.warning("Error looking for entity with config entry ID: %s", ex)

def generate_unique_entity_id(
        device_type: str,
        device_name: str | None,
        coordinator,
        attr_key: str,
        pv_string_idx: int | None = None,
) -> str:
    """Generate a unique ID for the entity."""

    # Use the device name if available, otherwise use the device type
    unique_device_part = generate_device_id(device_name, device_type)
    if pv_string_idx is not None:
        unique_id = f"{coordinator.hub.config_entry.entry_id}_{unique_device_part}_pv{pv_string_idx}_{attr_key}"
    else:
        unique_id = f"{coordinator.hub.config_entry.entry_id}_{unique_device_part}_{attr_key}"

    return unique_id

def generate_device_id(
    device_name: str | None,
    device_type: Optional[str] = None,
) -> str:
    """Generate a unique device ID based on the device name and type."""
    unique_device_part = str(device_name).lower().replace(' ', '_') if device_name else device_type
    return unique_device_part if unique_device_part else "unknown_device_id"

@dataclass(frozen=True)
class SigenergySensorEntityDescription(SensorEntityDescription):
    """Class describing Sigenergy sensor entities."""

    entity_registry_enabled_default: bool = True
    value_fn: Optional[Callable[[Any, Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Any]] = None
    extra_fn_data: Optional[bool] = False  # Flag to indicate if value_fn needs coordinator data
    extra_params: Optional[Dict[str, Any]] = None  # Additional parameters for value_fn
    source_entity_id: Optional[str] = None
    source_key: Optional[str] = None  # Key of the source entity to use for integration
    max_sub_interval: Optional[timedelta] = None
    round_digits: Optional[int] = None

    @classmethod
    def from_entity_description(cls, description,
                                    value_fn: Optional[Callable[[Any, Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Any]] = None,
                                    extra_fn_data: Optional[bool] = False,
                                    extra_params: Optional[Dict[str, Any]] = None):
        """Create a SigenergySensorEntityDescription instance from a SensorEntityDescription."""
        # Create a new instance with the base attributes
        if isinstance(description, cls):
            # If it's already our class, copy all attributes
            return cls(
				key=description.key,
				name=description.name,
				device_class=description.device_class,
				native_unit_of_measurement=description.native_unit_of_measurement,
				state_class=description.state_class,
				entity_registry_enabled_default=description.entity_registry_enabled_default,
				value_fn=value_fn or description.value_fn,
				extra_fn_data=extra_fn_data if extra_fn_data is not None else description.extra_fn_data,
				extra_params=extra_params or description.extra_params,
				source_entity_id=description.source_entity_id,
				source_key=description.source_key,
				max_sub_interval=description.max_sub_interval,
				round_digits=description.round_digits,
				suggested_display_precision=description.suggested_display_precision,
			)
        # It's a regular SensorEntityDescription
        return cls(
            key=description.key,
            name=description.name,
            device_class=getattr(description, "device_class", None),
            native_unit_of_measurement=getattr(description, "native_unit_of_measurement", None),
            state_class=getattr(description, "state_class", None),
            entity_registry_enabled_default=getattr(description, "entity_registry_enabled_default", True),
            value_fn=value_fn,
            extra_fn_data=extra_fn_data,
            extra_params=extra_params,
        )

def safe_float(value: Any, precision: int = 6) -> Optional[float]:
    """Convert to float only if possible, else None."""
    try:
        if value is None:
            return 0.0
        if isinstance(value, float):
            return round(value, precision)
        if isinstance(value, int):
            return round(float(value), precision)
        else:
            return round(float(str(value)), precision)
    except (InvalidOperation, TypeError, ValueError):
        _LOGGER.warning("Could not convert value %s (type %s) to float", value, type(value).__name__)
        return None
    
def safe_decimal(value: Any) -> Optional[Decimal]:
    """Convert to Decimal only if possible, else None."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _LOGGER.warning("Could not convert value %s (type %s) to Decimal", value, type(value).__name__)
        return None