"""The Sigenergy ESS integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SigenergyDataUpdateCoordinator
from .modbus import SigenergyModbusHub
from .const import CONF_PLANT_CONNECTION

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sigenergy ESS from a config entry."""
    _LOGGER.debug("async_setup_entry: Starting setup for entry: %s", entry.title)
    scan_interval = entry.data.get(CONF_PLANT_CONNECTION,
                                    {}).get(CONF_SCAN_INTERVAL, 
                                            DEFAULT_SCAN_INTERVAL)
    host = entry.data[CONF_PLANT_CONNECTION][CONF_HOST]
    port = entry.data[CONF_PLANT_CONNECTION][CONF_PORT]

    _LOGGER.debug("async_setup_entry: Scan interval set to %s seconds", scan_interval)
    _LOGGER.debug("async_setup_entry: CONF_PLANT_CONNECTION: %s", entry.data[CONF_PLANT_CONNECTION])

    hub = SigenergyModbusHub(hass, entry)
    _LOGGER.debug("async_setup_entry: SigenergyModbusHub created: %s", hub)

    coordinator = SigenergyDataUpdateCoordinator(
        hass,
        _LOGGER,
        hub=hub,
        name=f"{DOMAIN}_{host}_{port}",
        scan_interval=scan_interval,
    )
    _LOGGER.debug("async_setup_entry: SigenergyDataUpdateCoordinator created: %s", coordinator)

    try:
        _LOGGER.debug("async_setup_entry: Connecting to Modbus hub and performing first refresh...")
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug("async_setup_entry: Modbus hub connected and coordinator first refresh completed")
    except ConfigEntryNotReady:
        # This exception is already logged by the coordinator
        # We can optionally add more context here if needed
        _LOGGER.error("async_setup_entry: Initial data fetch failed. Deferring setup for %s.", entry.title)
        # The coordinator's first refresh will re-raise ConfigEntryNotReady, which is what we want.
        raise
    except Exception as ex:
        _LOGGER.error(
            "async_setup_entry: An unexpected error occurred during initial setup for %s: %s",
            entry.title,
            ex,
            exc_info=True
        )
        # Disconnect the hub before raising the exception
        await hub.async_close()
        raise ConfigEntryNotReady(f"Unexpected error during setup: {ex}") from ex


    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "hub": hub,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    _LOGGER.debug("async_setup_entry: Setup completed successfully for entry: %s", entry.title)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hub = hass.data[DOMAIN][entry.entry_id]["hub"]
        await hub.async_close()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

async def async_migrate_entry(hass, entry: ConfigEntry):
    """Migrate old entry version."""
    _LOGGER.debug("Migrating configuration from version %s.%s", entry.version, entry.minor_version)

    if entry.version > 2 and entry.minor_version > 0:
        # This means the user has downgraded from a future version
        return False

    if entry.version == 1:
        new_data = {**entry.data}
        plant_connection = new_data.get(CONF_PLANT_CONNECTION, {}).copy()
        
        # Get the old high scan interval, or the default if not present
        scan_interval = plant_connection.get("scan_interval_high", DEFAULT_SCAN_INTERVAL)
        
        # Set the new scan interval
        plant_connection[CONF_SCAN_INTERVAL] = scan_interval
        
        # Remove old scan interval keys
        plant_connection.pop("scan_interval_high", None)
        plant_connection.pop("scan_interval_alarm", None)
        plant_connection.pop("scan_interval_medium", None)
        plant_connection.pop("scan_interval_low", None)
        
        new_data[CONF_PLANT_CONNECTION] = plant_connection

        hass.config_entries.async_update_entry(entry, data=new_data, minor_version=0, version=2)

    _LOGGER.debug("Migration to configuration version %s.%s successful", \
                  entry.version, entry.minor_version)

    return True
