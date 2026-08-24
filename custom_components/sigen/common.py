"""The Sigenergy ESS integration. Common code."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Callable, Dict, Literal
from dataclasses import dataclass
from homeassistant.helpers.entity_registry import (
    RegistryEntryHider,
    async_entries_for_config_entry,
    async_get as async_get_entity_registry,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.sensor import (
    SensorEntityDescription,
)

from .const import (DOMAIN, DEVICE_TYPE_INVERTER, DEVICE_TYPE_DC_CHARGER)
from .modbusregisterdefinitions import DCChargerRunningState

_LOGGER = logging.getLogger(__name__)


def ac_charger_command_available(data: Dict[str, Any], identifier: Optional[Any]) -> bool:
    """Return if AC charger start/stop commands should be exposed."""
    state = data.get("ac_chargers", {}).get(identifier, {}).get("ac_charger_system_state")
    return state is not None and state not in (0, 1)


def dc_charger_command_available(data: Dict[str, Any], identifier: Optional[Any]) -> bool:
    """Return if DC charger start/stop commands should be exposed."""
    charger_data = data.get("dc_chargers", {}).get(identifier)
    if not isinstance(charger_data, dict):
        return False

    state = charger_data.get("dc_charger_running_state")
    if state is None:
        # The command register is write-only and some chargers do not expose the
        # optional running-state register. Keep the commands usable when other DC
        # charger telemetry confirms that the configured charger is responding.
        return any(value is not None for value in charger_data.values())

    return state is not None and state not in (
        DCChargerRunningState.IDLE,
        DCChargerRunningState.UNAVAILABLE,
    )


def get_suffix_if_not_one(name: str) -> str:
    """Get the last part of the name if it is a number other than 1."""
    return name.split()[-1].strip() + " " if len(name.split()) > 1 and name.split()[-1].isdigit() and name.split()[-1] != "1" else ""


def resolve_register_support_key(
    register_name: str, pv_string_idx: Optional[int] = None
) -> str:
    """Resolve an entity description key to its Modbus register name."""
    if pv_string_idx is not None and register_name in {"voltage", "current"}:
        return f"inverter_pv{pv_string_idx}_{register_name}"
    return register_name


def resolve_register_support_keys(
    description: Any, pv_string_idx: Optional[int] = None
) -> tuple[str, ...]:
    """Resolve all Modbus registers required by an entity description."""
    explicit_keys = getattr(description, "register_support_keys", None)
    if explicit_keys is not None:
        # An explicit empty tuple opts a non-readable entity, such as a command
        # backed by a write-only register, out of inferred support filtering.
        register_names = (
            (explicit_keys,) if isinstance(explicit_keys, str) else tuple(explicit_keys)
        )
    else:
        extra_params = getattr(description, "extra_params", None) or {}
        register_name = extra_params.get("register_name")
        source_key = getattr(description, "source_key", None)

        if register_name:
            register_names = (register_name,)
        elif source_key == "pv_string_power" and pv_string_idx is not None:
            register_names = ("voltage", "current")
        elif source_key:
            register_names = (source_key,)
        else:
            register_names = (description.key,)

    return tuple(
        resolve_register_support_key(register_name, pv_string_idx)
        for register_name in register_names
    )


def get_entity_register_support(
    hub,
    device_type: str,
    device_name: Optional[str],
    description: Any,
    pv_string_idx: Optional[int] = None,
) -> tuple[Optional[bool], tuple[str, ...]]:
    """Return aggregate support and backing register keys for an entity."""
    if getattr(description, "register_support_scope", "entity") == "inverters":
        support_targets = tuple(
            (DEVICE_TYPE_INVERTER, inverter_name)
            for inverter_name in hub.inverter_connections
        )
    else:
        support_targets = ((device_type, device_name),)

    register_support_alternatives = getattr(
        description, "register_support_alternatives", None
    )
    if register_support_alternatives:
        resolved_alternatives = tuple(
            tuple(
                resolve_register_support_key(register_name, pv_string_idx)
                for register_name in alternative
            )
            for alternative in register_support_alternatives
        )
        register_support_keys = tuple(
            dict.fromkeys(
                register_name
                for alternative in resolved_alternatives
                for register_name in alternative
            )
        )
        alternative_states = []
        for alternative in resolved_alternatives:
            states = tuple(
                hub.get_register_support(
                    support_device_type, support_device_name, register_name
                )
                for support_device_type, support_device_name in support_targets
                for register_name in alternative
            )
            if not states:
                alternative_states.append(None)
            elif any(state is False for state in states):
                alternative_states.append(False)
            elif all(state is True for state in states):
                alternative_states.append(True)
            else:
                alternative_states.append(None)

        if any(state is True for state in alternative_states):
            return True, register_support_keys
        if alternative_states and all(
            state is False for state in alternative_states
        ):
            return False, register_support_keys
        return None, register_support_keys

    register_support_keys = resolve_register_support_keys(
        description, pv_string_idx
    )
    if not register_support_keys:
        # Explicitly dependency-free entities are structurally supported. This
        # also restores registry entries hidden by an earlier dependency rule;
        # their runtime availability is still decided by the entity itself.
        return True, register_support_keys

    support_states = tuple(
        hub.get_register_support(
            support_device_type, support_device_name, register_name
        )
        for support_device_type, support_device_name in support_targets
        for register_name in register_support_keys
    )

    if not support_states:
        return None, register_support_keys

    if getattr(description, "register_support_mode", "all") == "any":
        if any(state is True for state in support_states):
            return True, register_support_keys
        if all(state is False for state in support_states):
            return False, register_support_keys
        return None, register_support_keys

    if any(state is False for state in support_states):
        return False, register_support_keys
    if all(state is True for state in support_states):
        return True, register_support_keys
    return None, register_support_keys


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
    entity_registry = async_get_entity_registry(coordinator.hass)
    registry_entries_by_unique_id = {}
    for registry_entry in async_entries_for_config_entry(
        entity_registry, coordinator.hub.config_entry.entry_id
    ):
        registry_entries_by_unique_id.setdefault(
            registry_entry.unique_id, []
        ).append(registry_entry)

    entities = []
    for description in entity_description:
        # _LOGGER.debug("Generating entity for description: %s", description.name)

        register_support, register_support_keys = get_entity_register_support(
            coordinator.hub,
            device_type,
            device_name,
            description,
            pv_string_idx,
        )
        if register_support is False:
            unique_id = generate_unique_entity_id(
                device_type,
                device_name,
                coordinator,
                description.key,
                pv_string_idx,
            )
            for registry_entry in registry_entries_by_unique_id.get(unique_id, []):
                if registry_entry.hidden_by is None:
                    entity_registry.async_update_entity(
                        registry_entry.entity_id,
                        hidden_by=RegistryEntryHider.INTEGRATION,
                    )
                    _LOGGER.info(
                        "Hid unsupported entity %s while preserving its registry entry",
                        registry_entry.entity_id,
                    )
            _LOGGER.debug(
                "Skipping entity '%s' because backing register(s) '%s' are "
                "unsupported by %s",
                description.name,
                ", ".join(register_support_keys),
                device_name,
            )
            continue

        if register_support is True:
            unique_id = generate_unique_entity_id(
                device_type,
                device_name,
                coordinator,
                description.key,
                pv_string_idx,
            )
            for registry_entry in registry_entries_by_unique_id.get(unique_id, []):
                if registry_entry.hidden_by is RegistryEntryHider.INTEGRATION:
                    entity_registry.async_update_entity(
                        registry_entry.entity_id,
                        hidden_by=None,
                    )
                    _LOGGER.info(
                        "Unhid supported entity %s",
                        registry_entry.entity_id,
                    )

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
            # Check if device_name already contains "DC Charger" to avoid double naming
            if "DC Charger" in device_name:
                sensor_id = device_name
            else:
                sensor_id = f"{device_name} DC Charger"
            sensor_name = f"{sensor_id} {description.name}"
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
            _LOGGER.debug("unique ID pattern constructed from: \n config_entry_id: %s \n device_type: %s \n device_name: %s \n source_key: %s \n source_attr_key: %s \n pv_idx: %s",
                            coordinator.hub.config_entry.entry_id, device_type, device_name, source_key, source_attr_key, pv_string_idx)
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
    register_support_keys: Optional[tuple[str, ...]] = None
    register_support_alternatives: Optional[tuple[tuple[str, ...], ...]] = None
    register_support_scope: Literal["entity", "inverters"] = "entity"
    register_support_mode: Literal["all", "any"] = "all"
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
				register_support_keys=description.register_support_keys,
				register_support_alternatives=description.register_support_alternatives,
				register_support_scope=description.register_support_scope,
				register_support_mode=description.register_support_mode,
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
            register_support_keys=getattr(description, "register_support_keys", None),
            register_support_alternatives=getattr(
                description, "register_support_alternatives", None
            ),
            register_support_scope=getattr(
                description, "register_support_scope", "entity"
            ),
            register_support_mode=getattr(description, "register_support_mode", "all"),
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
        if value is None:
            return Decimal(0.0)
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _LOGGER.warning("Could not convert value %s (type %s) to Decimal", value, type(value).__name__)
        return None
