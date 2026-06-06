"""Switch platform for Sigenergy ESS integration."""
from __future__ import annotations
import logging
import asyncio
import time
from dataclasses import dataclass
from typing import Any, Coroutine, Callable, Dict, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from .common import generate_sigen_entity, generate_device_id
from .const import (
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
    CONF_INVERTER_HAS_DCCHARGER,
)
from .coordinator import SigenergyDataUpdateCoordinator # Import coordinator
from .sigen_entity import SigenergyEntity # Import the new base class

_LOGGER = logging.getLogger(__name__)

# Grace window for active states (3/4/5) to allow hardware to transition
# after a write, before reverting to reporting the real hardware state.
_AC_CHARGER_FORCE_OFF_ACTIVE_GRACE = 120


@dataclass(frozen=True)
class SigenergySwitchEntityDescription(SwitchEntityDescription):
    """Class describing Sigenergy switch entities."""

    # Provide default lambdas instead of None to satisfy type checker
    # The second argument 'identifier' will be device_name for inverters, device_id otherwise
    is_on_fn: Callable[[Dict[str, Any], Optional[Any]], bool] = lambda data, identifier: False # Remains synchronous
    # Make turn_on/off functions async and update type hint
    # Make turn_on/off functions async and update type hint to accept coordinator
    turn_on_fn: Callable[[SigenergyDataUpdateCoordinator, Optional[Any]], Coroutine[Any, Any, None]] = lambda coordinator, identifier: asyncio.sleep(0) # Placeholder async lambda
    turn_off_fn: Callable[[SigenergyDataUpdateCoordinator, Optional[Any]], Coroutine[Any, Any, None]] = lambda coordinator, identifier: asyncio.sleep(0) # Placeholder async lambda
    available_fn: Callable[[Dict[str, Any], Optional[Any]], bool] = lambda data, _: True
    entity_registry_enabled_default: bool = True


PLANT_SWITCHES: list[SigenergySwitchEntityDescription] = [
    SigenergySwitchEntityDescription(
        key="plant_start_stop",
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
AC_CHARGER_SWITCHES: list[SigenergySwitchEntityDescription] = [
    SigenergySwitchEntityDescription(
        key="ac_charger_start_stop",
        name="AC Charger Power",
        icon="mdi:ev-station",
        # identifier here will be ac_charger_name
        is_on_fn=lambda data, identifier: data.get("ac_chargers", {}).get(identifier, {}).get("ac_charger_system_state") in (2,3,4,5),
        # Check if EV is connected (State != 0 (Init) and != 1 (A1_A2))
        available_fn=lambda data, identifier: data.get("ac_chargers", {}).get(identifier, {}).get("ac_charger_system_state") not in (None, 0, 1),
        turn_on_fn=lambda coordinator, identifier: coordinator.async_write_parameter("ac_charger", identifier, "ac_charger_start_stop", 0),
        turn_off_fn=lambda coordinator, identifier: coordinator.async_write_parameter("ac_charger", identifier, "ac_charger_start_stop", 1),
    ),
]

DC_CHARGER_SWITCHES: list[SigenergySwitchEntityDescription] = [
    SigenergySwitchEntityDescription(
        key="dc_charging",
        name="DC Charging",
        icon="mdi:ev-station",
        # CHANGED: is_on_fn now checks != 0 to reflect both charging (positive) and discharging (negative) states
        is_on_fn=lambda data, identifier: (data.get("dc_chargers", {}).get(identifier, {}).get("dc_charger_output_power", 0) or 0) != 0,
        turn_on_fn=lambda coordinator, identifier: coordinator.async_write_parameter("dc_charger", identifier, "dc_charger_start_stop", 0),
        turn_off_fn=lambda coordinator, identifier: coordinator.async_write_parameter("dc_charger", identifier, "dc_charger_start_stop", 1),
    ),
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
                                entity_descriptions, device_type, **kwargs):
        entities_to_add.extend(
            generate_sigen_entity(
                plant_name,
                device_name,
                device_conn,
                coordinator,
                SigenergySwitch,
                entity_descriptions,
                device_type,
                **kwargs,
            )
        )

    # Plant Switches
    add_entities_for_device(None, None, PLANT_SWITCHES, DEVICE_TYPE_PLANT)

    # Inverter and related switches
    for device_name, device_conn in coordinator.hub.inverter_connections.items():
        add_entities_for_device(device_name, device_conn, INVERTER_SWITCHES, DEVICE_TYPE_INVERTER)

        # DC Charger
        if device_conn.get(CONF_INVERTER_HAS_DCCHARGER, False):
            dc_name = f"{device_name} DC Charger"
            parent_inverter_id = f"{coordinator.hub.config_entry.entry_id}_{generate_device_id(device_name)}"
            dc_id = f"{parent_inverter_id}_dc_charger"
            dc_device_info = DeviceInfo(
                identifiers={(DOMAIN, dc_id)},
                name=dc_name,
                manufacturer="Sigenergy",
                model="DC Charger",
                via_device=(DOMAIN, parent_inverter_id),
            )
            add_entities_for_device(device_name, device_conn, DC_CHARGER_SWITCHES, DEVICE_TYPE_DC_CHARGER, device_info=dc_device_info)

    # AC Charger Switches
    for device_name, device_conn in coordinator.hub.ac_charger_connections.items():
        add_entities_for_device(device_name, device_conn, AC_CHARGER_SWITCHES, DEVICE_TYPE_AC_CHARGER)

    if entities_to_add:
        async_add_entities(entities_to_add)
        _LOGGER.debug("Added %d switch entities", len(entities_to_add))
    else:
        _LOGGER.debug("No switch entities to add.")


class SigenergySwitch(SigenergyEntity, SwitchEntity, RestoreEntity):
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
        # Optimistic state tracking for AC charger (#349 / #365)
        self._ac_charger_force_off: bool = False
        self._ac_charger_force_off_at: float = 0.0  # time.monotonic() timestamp

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        if self.entity_description.key == "ac_charger_start_stop":
            state = await self.async_get_last_state()
            if state is not None:
                # Restore the force-off flag if it was active
                force_off = state.attributes.get("ac_charger_force_off", False)
                force_off_at_utc = state.attributes.get("ac_charger_force_off_at_utc")
                if force_off and force_off_at_utc is not None:
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.fromisoformat(force_off_at_utc)
                        elapsed_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
                        
                        # Recreate the monotonic timestamp
                        self._ac_charger_force_off = True
                        self._ac_charger_force_off_at = time.monotonic() - elapsed_seconds
                        _LOGGER.debug(
                            "Restored AC charger force-off state for %s. Created %.1fs ago",
                            self.entity_id,
                            elapsed_seconds,
                        )
                    except Exception as ex:
                        _LOGGER.warning(
                            "Failed to restore AC charger force-off timestamp for %s: %s",
                            self.entity_id,
                            ex,
                        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        attrs = super().extra_state_attributes or {}
        if self.entity_description.key == "ac_charger_start_stop" and self._ac_charger_force_off:
            from datetime import datetime, timezone, timedelta
            elapsed = time.monotonic() - self._ac_charger_force_off_at
            utc_time = datetime.now(timezone.utc) - timedelta(seconds=elapsed)
            return {
                **attrs,
                "ac_charger_force_off": True,
                "ac_charger_force_off_at_utc": utc_time.isoformat(),
            }
        return attrs

    def _clear_ac_charger_force_off(self) -> None:
        """Clear the optimistic force-off flag if set."""
        if self._ac_charger_force_off:
            _LOGGER.debug(
                "Clearing AC charger force-off flag for %s",
                self.entity_id,
            )
            self._ac_charger_force_off = False
            self._ac_charger_force_off_at = 0.0

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False

        # Use device_name as the primary identifier passed to the lambda/function
        identifier = self._device_name
        result = self.entity_description.available_fn(self.coordinator.data, identifier)
        if not result and self.entity_description.key == "ac_charger_start_stop":
            # Check why it's unavailable.
            # If it's a true terminal disconnection (state 0 or 1), clear the flag.
            # If it's None (read failure), do NOT clear the flag.
            if self.coordinator.data is not None:
                state = (
                    self.coordinator.data.get("ac_chargers", {})
                    .get(identifier, {})
                    .get("ac_charger_system_state")
                )
                if state in (0, 1):
                    self._clear_ac_charger_force_off()
        return result

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        if self.coordinator.data is None:
            return None
        identifier = self._device_name
        result = self.entity_description.is_on_fn(self.coordinator.data, identifier)

        if self.entity_description.key == "ac_charger_start_stop" and self._ac_charger_force_off:
            state = (
                self.coordinator.data.get("ac_chargers", {})
                .get(identifier, {})
                .get("ac_charger_system_state")
            )
            elapsed = time.monotonic() - self._ac_charger_force_off_at

            if state in (6, 7):
                # Definitive non-ON state — the charger faulted or errored.
                # Clear the flag and report the real state.
                self._clear_ac_charger_force_off()
            elif state is None:
                # Read failure / missing data — keep flag, return False (entity will show as unavailable via available_fn)
                return False
            elif state in (3, 4, 5):
                # Active states (Preparing, EV Ready, Charging)
                if elapsed > _AC_CHARGER_FORCE_OFF_ACTIVE_GRACE:
                    # Beyond the transition window, this is a real active state.
                    # Clear override and show actual state.
                    _LOGGER.debug(
                        "AC charger in active state %s after grace window (%.1fs) for %s, "
                        "clearing force-off",
                        state,
                        elapsed,
                        self.entity_id,
                    )
                    self._clear_ac_charger_force_off()
                else:
                    # Within the transition window, override to OFF to cover stale reads
                    return False
            elif state == 2:
                # State is 2 (Reserving) — override to OFF.
                # Do NOT clear the flag due to TTL while state is 2.
                return False
            else:
                # State is 0 or 1 (Initializing, Not Connected).
                # Clear the flag and report the real state.
                self._clear_ac_charger_force_off()

        return result

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self.coordinator.data is None:
            raise HomeAssistantError(f"Cannot turn on {self.entity_id}: Coordinator data is unavailable")
        identifier = self._device_name

        # Snapshot the flag so we can restore on failure
        is_ac_charger = self.entity_description.key == "ac_charger_start_stop"
        prev_force_off = self._ac_charger_force_off
        prev_force_off_at = self._ac_charger_force_off_at

        # Tentatively clear — user wants the charger on
        if is_ac_charger:
            self._clear_ac_charger_force_off()

        try:
            await self.entity_description.turn_on_fn(self.coordinator, identifier)
        except Exception:
            # Write failed — restore the previous flag
            if is_ac_charger:
                self._ac_charger_force_off = prev_force_off
                self._ac_charger_force_off_at = prev_force_off_at
            raise

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self.coordinator.data is None:
            raise HomeAssistantError(f"Cannot turn off {self.entity_id}: Coordinator data is unavailable")
        identifier = self._device_name

        # Set optimistic flag BEFORE write — the write triggers an internal
        # refresh that may publish state 2 as ON before we return.
        is_ac_charger = self.entity_description.key == "ac_charger_start_stop"
        if is_ac_charger:
            self._ac_charger_force_off = True
            self._ac_charger_force_off_at = time.monotonic()

        try:
            await self.entity_description.turn_off_fn(self.coordinator, identifier)
        except Exception:
            # Write failed — roll back the flag
            if is_ac_charger:
                self._clear_ac_charger_force_off()
            raise

        await self.coordinator.async_request_refresh()
