"""Modbus communication for Sigenergy ESS.

Previously this hub owned its own pymodbus client pool: one
`AsyncModbusTcpClient` per (host, port), its own connect/reconnect/lock
bookkeeping, and a pymodbus-version compatibility shim for the
`slave`/`device_id` parameter rename pymodbus made in 3.10. Home Assistant's
Modbus integration now provides that connection sharing itself
(`async_get_unit`), and a device's register layout is expressed as
`modbus_connection` `Component`s (see `sigen_components.py` /
`sigen_devices.py`) rather than ad hoc pymodbus calls.

This hub is now a thin adapter: it turns this config entry's connection
dicts into `SigenergyPlant` / `SigenergyInverter` / `SigenergyACCharger`
device objects (one `async_get_unit()` hold per physical connection, shared
automatically with any other integration using the same device) and keeps
the public surface `coordinator.py`, the entity platforms and
`diagnostics.py` already depend on.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry  # pylint: disable=no-name-in-module, syntax-error
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from modbus_connection import ModbusConnectionError, ModbusError, ModbusTcpParams

from .const import (
    CONF_AC_CHARGER_CONNECTIONS,
    CONF_HOST,
    CONF_INVERTER_CONNECTIONS,
    CONF_INVERTER_HAS_DCCHARGER,
    CONF_PLANT_CONNECTION,
    CONF_PLANT_ID,
    CONF_PORT,
    CONF_READ_ONLY,
    CONF_SLAVE_ID,
    DEFAULT_PLANT_SLAVE_ID,
    DEFAULT_READ_ONLY,
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
)
from .sigen_devices import PROBE_RETRY_DELAY, SigenergyACCharger, SigenergyInverter, SigenergyPlant

_LOGGER = logging.getLogger(__name__)


class SigenergyModbusError(HomeAssistantError):
    """Exception for Sigenergy Modbus errors."""


#: Minimum per-request timeout for every Sigenergy unit, matching the old
#: hub's explicit `AsyncModbusTcpClient(timeout=20, ...)`. modbus_connection's
#: own default is 10s, which is tight enough for a large plant register block
#: that a slow answer can arrive after the client has already given up on it
#: -- surfacing as a "Received unexpected response" transaction-ID mismatch
#: and a dropped connection.
_REQUEST_TIMEOUT = 20.0


def _connection_params(device_info: Dict[str, Any]) -> ModbusTcpParams:
    """Build the Modbus TCP link parameters for one connection dict."""
    return ModbusTcpParams(host=device_info[CONF_HOST], port=device_info[CONF_PORT])


def _acquire_unit(
    hass: HomeAssistant, config_entry: ConfigEntry, device_info: Dict[str, Any], slave_id: int
):
    """Acquire a shared Modbus unit for one connection, with our timeout floor."""
    unit = async_get_unit(hass, config_entry, _connection_params(device_info), slave_id)
    unit.require_timeout(_REQUEST_TIMEOUT)
    return unit


class SigenergyModbusHub:
    """Build and hold the Sigenergy device objects for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the hub and acquire a shared Modbus unit per connection."""
        self.hass = hass
        self.config_entry = config_entry

        # Kept for diagnostics.py, which surfaces the probe retry cadence.
        self.probe_retry_delay = PROBE_RETRY_DELAY

        # Store connection for plant
        self.plant_connection = config_entry.data.get(CONF_PLANT_CONNECTION, {})
        self._plant_host = self.plant_connection[CONF_HOST]
        self._plant_port = self.plant_connection[CONF_PORT]
        self.plant_id = self.plant_connection.get(CONF_PLANT_ID, DEFAULT_PLANT_SLAVE_ID)

        # Read-only mode setting
        self.read_only = self.plant_connection.get(CONF_READ_ONLY, DEFAULT_READ_ONLY)

        # Get inverter connections
        self.inverter_connections = config_entry.data.get(CONF_INVERTER_CONNECTIONS, {})
        _LOGGER.debug("Inverter connections: %s", self.inverter_connections)
        self.inverter_count = len(self.inverter_connections)

        # Get AC Charger connections
        self.ac_charger_connections = config_entry.data.get(CONF_AC_CHARGER_CONNECTIONS, {})
        _LOGGER.debug("AC Charger connections: %s", self.ac_charger_connections)
        self.ac_charger_count = len(self.ac_charger_connections)

        plant_slave_id = self.plant_connection.get(CONF_SLAVE_ID, self.plant_id)
        self._plant = SigenergyPlant(
            _acquire_unit(hass, config_entry, self.plant_connection, plant_slave_id)
        )

        self._inverters: Dict[str, SigenergyInverter] = {
            name: SigenergyInverter(
                _acquire_unit(hass, config_entry, info, info[CONF_SLAVE_ID]),
                has_dc_charger=info.get(CONF_INVERTER_HAS_DCCHARGER, False),
            )
            for name, info in self.inverter_connections.items()
        }

        self._ac_chargers: Dict[str, SigenergyACCharger] = {
            name: SigenergyACCharger(_acquire_unit(hass, config_entry, info, info[CONF_SLAVE_ID]))
            for name, info in self.ac_charger_connections.items()
        }

    async def async_close(self) -> None:
        """Release this hub's Modbus units.

        `async_get_unit()` already registers its release on the config
        entry's own unload callbacks (`entry.async_on_unload`), which have
        already run by the time `async_unload_entry` calls this. Kept so the
        existing `__init__.py` call site needs no change.
        """
        return

    def get_register_support(
        self, device_type: str, device_name: Optional[str], register_name: str
    ) -> Optional[bool]:
        """Return True, False, or None for a device register's support state."""
        if device_type == DEVICE_TYPE_PLANT:
            return self._plant.is_supported(register_name)
        if device_type == DEVICE_TYPE_INVERTER:
            inverter = self._inverters.get(device_name or "")
            return inverter.is_supported(register_name) if inverter else None
        if device_type == DEVICE_TYPE_AC_CHARGER:
            ac_charger = self._ac_chargers.get(device_name or "")
            return ac_charger.is_supported(register_name) if ac_charger else None
        if device_type == DEVICE_TYPE_DC_CHARGER:
            inverter_name = (device_name or "").replace(" DC Charger", "").strip()
            inverter = self._inverters.get(inverter_name)
            return inverter.is_supported(register_name) if inverter else None
        return None

    async def async_read_plant_data(self) -> Dict[str, Any]:
        """Read all supported plant data."""
        return await self._async_read(self._plant.async_update(), "plant")

    async def async_read_inverter_data(self, inverter_name: str) -> Dict[str, Any]:
        """Read all supported inverter data (DC charger excluded)."""
        inverter = self._inverters.get(inverter_name)
        if inverter is None:
            _LOGGER.error("Unknown inverter name provided for reading data: %s", inverter_name)
            return {}
        return await self._async_read(
            inverter.async_update_inverter(), f"inverter '{inverter_name}'"
        )

    async def async_read_dc_charger_data(self, inverter_name: str) -> Dict[str, Any]:
        """Read all supported DC charger data."""
        inverter = self._inverters.get(inverter_name)
        if inverter is None:
            _LOGGER.error(
                "Unknown inverter name provided for reading DC Charger data: %s", inverter_name
            )
            return {}
        return await self._async_read(
            inverter.async_update_dc_charger(), f"DC charger on inverter '{inverter_name}'"
        )

    async def async_read_ac_charger_data(self, ac_charger_name: str) -> Dict[str, Any]:
        """Read all supported AC charger data."""
        ac_charger = self._ac_chargers.get(ac_charger_name)
        if ac_charger is None:
            _LOGGER.error("Unknown AC charger name provided for reading data: %s", ac_charger_name)
            return {}
        return await self._async_read(
            ac_charger.async_update(), f"AC charger '{ac_charger_name}'"
        )

    @staticmethod
    async def _async_read(coro, description: str) -> Dict[str, Any]:
        """Await a device's `async_update()`, translating connection errors.

        Raises `SigenergyModbusError` if the link itself is down; any other
        per-register/per-component failure is already handled inside the
        device object, which returns a best-effort (possibly partial) dict.
        """
        try:
            return await coro
        except ModbusConnectionError as ex:
            raise SigenergyModbusError(f"Connection error reading {description}: {ex}") from ex
        except ModbusError as ex:
            raise SigenergyModbusError(f"Modbus error reading {description}: {ex}") from ex

    async def async_write_parameter(
        self,
        device_type: str,
        device_identifier: Optional[str],
        register_name: str,
        value: Union[int, float, str],
    ) -> None:
        """Write a parameter to a specified device (plant, inverter, ac/dc charger).

        Args:
            device_type: The type of device ('plant', 'inverter', 'ac_charger', 'dc_charger').
            device_identifier: The inverter or AC charger name, or None for the plant.
                For 'dc_charger' this is the parent inverter's bare name (no suffix).
            register_name: The name of the parameter register to write.
            value: The value to write to the register.

        Raises:
            SigenergyModbusError: If writing is disabled (read-only mode), the device/parameter
                                  is unknown, or a Modbus error occurs.
            ValueError: If device_type is invalid or device_identifier is missing when required.
        """
        if self.read_only:
            _LOGGER.error("Cannot write parameter while in read-only mode")
            return

        _LOGGER.debug(
            "Writing %s parameter '%s' with value %s to device '%s'",
            device_type, register_name, value, device_identifier or "plant",
        )

        try:
            if device_type == DEVICE_TYPE_PLANT:
                await self._plant.async_write(register_name, value)
            elif device_type in (DEVICE_TYPE_INVERTER, DEVICE_TYPE_DC_CHARGER):
                if not device_identifier or device_identifier not in self._inverters:
                    raise SigenergyModbusError(f"Unknown inverter name: {device_identifier}")
                await self._inverters[device_identifier].async_write(register_name, value)
            elif device_type == DEVICE_TYPE_AC_CHARGER:
                if not device_identifier or device_identifier not in self._ac_chargers:
                    raise SigenergyModbusError(f"Unknown ac_charger name: {device_identifier}")
                await self._ac_chargers[device_identifier].async_write(register_name, value)
            else:
                raise ValueError(f"Unknown device_type: {device_type}")
        except AttributeError as ex:
            raise SigenergyModbusError(
                f"Unknown or unsupported {device_type} parameter '{register_name}': {ex}"
            ) from ex
        except ModbusConnectionError as ex:
            raise SigenergyModbusError(f"Connection error: {ex}") from ex
        except ModbusError as ex:
            raise SigenergyModbusError(f"Modbus error: {ex}") from ex
        except SigenergyModbusError:
            raise
        except Exception as ex:
            _LOGGER.error(
                "Failed to write %s parameter '%s' (device: %s): %s",
                device_type, register_name, device_identifier or "plant", ex,
            )
            raise
