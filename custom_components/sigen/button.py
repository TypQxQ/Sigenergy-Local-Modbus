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
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .common import generate_sigen_entity
from .const import (
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
        identifier = self._device_name
        await self.entity_description.press_fn(self.coordinator, identifier)
