"""Switch platform for Sigenergy ESS integration."""
from __future__ import annotations
import logging
import asyncio
from dataclasses import dataclass
from typing import Any, Coroutine, Callable, Dict, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.exceptions import HomeAssistantError

from .common import generate_sigen_entity
from .const import (
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
)
from .coordinator import SigenergyDataUpdateCoordinator # Import coordinator
from .sigen_entity import SigenergyEntity # Import the new base class

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SigenergySwitchEntityDescription(SwitchEntityDescription):
    """Class describing Sigenergy switch entities."""

    # Provide default lambdas instead of None to satisfy type checker
    # The second argument 'identifier' will be device_name for inverters, device_id otherwise
    is_on_fn: Callable[[Dict[str, Any], Optional[Any]], bool | None] = lambda data, identifier: None
    # Make turn_on/off functions async and update type hint
    # Make turn_on/off functions async and update type hint to accept coordinator
    turn_on_fn: Callable[[SigenergyDataUpdateCoordinator, Optional[Any]], Coroutine[Any, Any, None]] = lambda coordinator, identifier: asyncio.sleep(0) # Placeholder async lambda
    turn_off_fn: Callable[[SigenergyDataUpdateCoordinator, Optional[Any]], Coroutine[Any, Any, None]] = lambda coordinator, identifier: asyncio.sleep(0) # Placeholder async lambda
    entity_registry_enabled_default: bool = True
    register_support_keys: Optional[tuple[str, ...]] = None


PLANT_SWITCHES: list[SigenergySwitchEntityDescription] = [
    SigenergySwitchEntityDescription(
        key="plant_start_stop",
        register_support_keys=("plant_running_state",),
        name="Plant Power",
        icon="mdi:power",
        is_on_fn=lambda data, _: data["plant"].get("plant_running_state") == 1, # Sync
        turn_on_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_start_stop", 1),
        turn_off_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_start_stop", 0),
        entity_registry_enabled_default=False,
    ),
    SigenergySwitchEntityDescription(
        key="plant_remote_ems_enable",
        name="Remote EMS (Controlled by Home Assistant)",
        icon="mdi:home-assistant",
        is_on_fn=lambda data, _: data.get("plant", {}).get("plant_remote_ems_enable") == 1,
        turn_on_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_remote_ems_enable", 1),
        turn_off_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_remote_ems_enable", 0),
        entity_registry_enabled_default=False,
    ),
    SigenergySwitchEntityDescription(
        key="plant_independent_phase_power_control_enable",
        name="Independent Phase Power Control",
        icon="mdi:tune",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda data, _: data.get("plant", {}).get("plant_independent_phase_power_control_enable") == 1,
        turn_on_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_independent_phase_power_control_enable", 1),
        turn_off_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_independent_phase_power_control_enable", 0),
        entity_registry_enabled_default=False,
    ),
    SigenergySwitchEntityDescription(
        key="plant_ess_preheating_enable",
        name="ESS Preheating Enable",
        icon="mdi:radiator",
        is_on_fn=lambda data, _: data.get("plant", {}).get("plant_ess_preheating_enable") == 1,
        turn_on_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_ess_preheating_enable", 1),
        turn_off_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_ess_preheating_enable", 0),
        entity_registry_enabled_default=False,
    ),
    SigenergySwitchEntityDescription(
        key="plant_ess_preheating_advance_enable",
        name="ESS Preheating Advance Enable",
        icon="mdi:clock-fast",
        is_on_fn=lambda data, _: data.get("plant", {}).get("plant_ess_preheating_advance_enable") == 1,
        turn_on_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_ess_preheating_advance_enable", 1),
        turn_off_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_ess_preheating_advance_enable", 0),
        entity_registry_enabled_default=False,
    ),
]

INVERTER_SWITCHES: list[SigenergySwitchEntityDescription] = [
    SigenergySwitchEntityDescription(
        key="inverter_start_stop",
        register_support_keys=("inverter_running_state",),
        name="Inverter Power",
        icon="mdi:power",
        # Use device_name (inverter_name) instead of device_id (now passed as the second arg 'identifier')
        is_on_fn=lambda data, identifier: data.get("inverters", {}).get(identifier, {}).get("inverter_running_state") == 1,
        turn_on_fn=lambda coordinator, identifier: coordinator.async_write_parameter("inverter", identifier, "inverter_start_stop", 1),
        turn_off_fn=lambda coordinator, identifier: coordinator.async_write_parameter("inverter", identifier, "inverter_start_stop", 0),
        entity_registry_enabled_default=False,
    ),
    # Register 41500 (inverter_remote_ems_dispatch_enable) removed in Modbus v2.8
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy switch platform."""
    coordinator: SigenergyDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    plant_name = config_entry.data[CONF_NAME]
    entities_to_add = []

    # Helper to add entities to the list
    def add_entities_for_device(device_name, device_conn,
                                entity_descriptions, device_type):
        entities_to_add.extend(
            generate_sigen_entity(
                plant_name,
                device_name,
                device_conn,
                coordinator,
                SigenergySwitch,
                entity_descriptions,
                device_type,
            )
        )

    # Plant Switches
    add_entities_for_device(None, None, PLANT_SWITCHES, DEVICE_TYPE_PLANT)

    # Inverter switches
    for device_name, device_conn in coordinator.hub.inverter_connections.items():
        add_entities_for_device(device_name, device_conn, INVERTER_SWITCHES, DEVICE_TYPE_INVERTER)

    if entities_to_add:
        async_add_entities(entities_to_add)
        _LOGGER.debug("Added %d switch entities", len(entities_to_add))
    else:
        _LOGGER.debug("No switch entities to add.")


class SigenergySwitch(SigenergyEntity, SwitchEntity):
    """Representation of a Sigenergy switch."""

    entity_description: SigenergySwitchEntityDescription
    # Explicitly type coordinator here to override the generic base class type
    coordinator: SigenergyDataUpdateCoordinator

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SigenergySwitchEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the switch."""
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

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self._control_reading_available()

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        if not self._control_reading_available():
            return None
        identifier = self._device_name
        return self.entity_description.is_on_fn(self.coordinator.data, identifier)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self.coordinator.data is None or not self.available:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="coordinator_unavailable",
                translation_placeholders={"entity_id": self.entity_id},
            )
        identifier = self._device_name
        await self.entity_description.turn_on_fn(self.coordinator, identifier)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self.coordinator.data is None or not self.available:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="coordinator_unavailable",
                translation_placeholders={"entity_id": self.entity_id},
            )
        identifier = self._device_name
        await self.entity_description.turn_off_fn(self.coordinator, identifier)
