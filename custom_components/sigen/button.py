"""Button platform for Sigenergy ESS integration."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Coroutine, Callable, Dict, Optional

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .common import ac_charger_command_available, generate_sigen_entity
from .const import (
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
)
from .coordinator import SigenergyDataUpdateCoordinator
from .sigen_entity import SigenergyEntity

_LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class SigenergyButtonEntityDescription(ButtonEntityDescription):
    """Class describing Sigenergy button entities."""

    press_fn: Callable[[SigenergyDataUpdateCoordinator, Optional[Any]], Coroutine[Any, Any, None]] = lambda coordinator, identifier: asyncio.sleep(0)
    available_fn: Callable[[Dict[str, Any], Optional[Any]], bool] = lambda data, _: True


PLANT_BUTTONS: list[SigenergyButtonEntityDescription] = [
    SigenergyButtonEntityDescription(
        key="plant_grid_power_loss_lockout_alarm_clear",
        name="Grid Power Loss Lockout Alarm Clear",
        icon="mdi:alarm-check",
        press_fn=lambda coordinator, _: coordinator.async_write_parameter("plant", None, "plant_grid_power_loss_lockout_alarm_clear", 1),
        entity_category=EntityCategory.CONFIG,
    ),
]

AC_CHARGER_BUTTONS: list[SigenergyButtonEntityDescription] = [
    SigenergyButtonEntityDescription(
        key="ac_charger_start",
        name="Start Charging",
        icon="mdi:ev-plug-type2",
        press_fn=lambda coordinator, identifier: coordinator.async_write_parameter("ac_charger", identifier, "ac_charger_start_stop", 0),
        available_fn=ac_charger_command_available,
    ),
    SigenergyButtonEntityDescription(
        key="ac_charger_stop",
        name="Stop Charging",
        icon="mdi:ev-plug-type2-off",
        press_fn=lambda coordinator, identifier: coordinator.async_write_parameter("ac_charger", identifier, "ac_charger_start_stop", 1),
        available_fn=ac_charger_command_available,
    ),
]

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy button platform."""
    coordinator: SigenergyDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    plant_name = config_entry.data[CONF_NAME]

    entities = generate_sigen_entity(
        plant_name,
        None,
        None,
        coordinator,
        SigenergyButton,
        PLANT_BUTTONS,
        DEVICE_TYPE_PLANT,
    )

    for device_name, device_conn in coordinator.hub.ac_charger_connections.items():
        entities.extend(
            generate_sigen_entity(
                plant_name,
                device_name,
                device_conn,
                coordinator,
                SigenergyButton,
                AC_CHARGER_BUTTONS,
                DEVICE_TYPE_AC_CHARGER,
            )
        )

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d button entities", len(entities))

class SigenergyButton(SigenergyEntity, ButtonEntity):
    """Representation of a Sigenergy button."""

    entity_description: SigenergyButtonEntityDescription
    coordinator: SigenergyDataUpdateCoordinator

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SigenergyButtonEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the button."""
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
        if not super().available:
            return False
        identifier = self._device_name
        return self.entity_description.available_fn(self.coordinator.data, identifier)

    async def async_press(self) -> None:
        """Press the button."""
        if self.coordinator.data is None:
            raise HomeAssistantError(f"Cannot press {self.entity_id}: Coordinator data is unavailable")
        identifier = self._device_name
        await self.entity_description.press_fn(self.coordinator, identifier)
