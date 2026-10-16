"""Data update coordinator for Sigenergy ESS."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Dict, Optional, Union # Added Optional, Union

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed  # pylint: disable=syntax-error

from .modbus import SigenergyModbusHub, SigenergyModbusError # Added SigenergyModbusError
from .const import CONF_INVERTER_HAS_DCCHARGER, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

STARTUP_TIMEOUT = 180
UPDATE_TIMEOUT = 60


class SigenergyDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Sigenergy ESS."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        hub: SigenergyModbusHub,
        name: str,
        scan_interval: int,
    ) -> None:
        """Initialize."""
        self.hub = hub
        self.platforms = []
        self.largest_update_interval : float = 0.0
        self.latest_fetch_time: float = 0.0
        self.data: dict[str, Any] | None = None
        self._update_sequence = 0
        self._last_successful_update_sequence = 0
        self.startup_fetch_time: float | None = None
        self.update_timeout_count = 0
        self.update_failure_count = 0
        self.latest_update_timing: dict[str, Any] = {}

        if scan_interval <= 1:
            scan_interval = DEFAULT_SCAN_INTERVAL
            logger.warning("Invalid scan_interval <= 1, defaulting to %s", DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via Modbus library."""
        self._update_sequence += 1
        update_sequence = self._update_sequence
        device_tasks: set[asyncio.Task] = set()
        phase = "startup" if self._last_successful_update_sequence == 0 else "poll"
        timeout = STARTUP_TIMEOUT if phase == "startup" else UPDATE_TIMEOUT
        start_time = time.perf_counter()
        status = "cancelled"
        device_timings: dict[str, dict[str, Any]] = {}

        async def timed_read(device: str, read, *args) -> Dict[str, Any]:
            started = time.perf_counter()
            device_status = "cancelled"
            try:
                result = await read(*args)
                device_status = "success"
                return result
            except Exception:
                device_status = "error"
                raise
            finally:
                device_timings[device] = {
                    "duration": time.perf_counter() - started,
                    "status": device_status,
                }

        try:
            async with async_timeout.timeout(timeout):

                # Fetch all data at once
                plant_data = await timed_read("plant", self.hub.async_read_plant_data)

                # Fetch inverter and DC charger data concurrently
                inverter_tasks = {
                    name: asyncio.create_task(timed_read(
                        f"inverter:{name}", self.hub.async_read_inverter_data, name
                    ))
                    for name in self.hub.inverter_connections
                }
                dc_tasks = {
                    name: asyncio.create_task(timed_read(
                        f"dc_charger:{name}", self.hub.async_read_dc_charger_data, name
                    ))
                    for name, cfg in self.hub.inverter_connections.items()
                    if cfg.get(CONF_INVERTER_HAS_DCCHARGER, False)
                }
                device_tasks.update(inverter_tasks.values())
                device_tasks.update(dc_tasks.values())
                inverter_data: dict[str, Any] = {}
                for name, task in inverter_tasks.items():
                    try:
                        inverter_data[name] = await task
                    except Exception as e:
                        _LOGGER.error("Error fetching inverter %s data: %s", name, e)
                        inverter_data[name] = {}
                dc_charger_data: dict[str, Any] = {}
                for name, task in dc_tasks.items():
                    try:
                        dc_charger_data[name] = await task
                    except Exception as e:
                        _LOGGER.error("Error fetching DC charger %s data: %s", name, e)
                        dc_charger_data[name] = {}

                # Fetch AC charger data concurrently
                ac_tasks = {
                    name: asyncio.create_task(timed_read(
                        f"ac_charger:{name}", self.hub.async_read_ac_charger_data, name
                    ))
                    for name in self.hub.ac_charger_connections
                }
                device_tasks.update(ac_tasks.values())
                ac_charger_data: dict[str, Any] = {}
                for name, task in ac_tasks.items():
                    try:
                        ac_charger_data[name] = await task
                    except Exception as e:
                        _LOGGER.error("Error fetching AC charger %s data: %s", name, e)
                        ac_charger_data[name] = {}

                # Merge fetched data into existing coordinator data
                # Preserve the _sensors_initialized flag across updates
                sensors_initialized = self.data.get("_sensors_initialized", False) if self.data else False
                self.data = {
                    "plant": plant_data,
                    "inverters": inverter_data,
                    "ac_chargers": ac_charger_data,
                    "dc_chargers": dc_charger_data,
                    "_sensors_initialized": sensors_initialized,  # Preserve flag across updates
                }
                _LOGGER.debug("RBS-coordinator-106-coordinator-post Merged fetched, len: %s data: %s", len(self.data), self.data)
                
                # Return the updated, complete data structure
                status = "success"
                self._last_successful_update_sequence = update_sequence
                return self.data
        except asyncio.TimeoutError as exception:
            status = "timeout"
            self.update_timeout_count += 1
            self.update_failure_count += 1
            raise UpdateFailed("Timeout communicating with Sigenergy system") from exception
        except Exception as exception:
            status = "error"
            self.update_failure_count += 1
            raise UpdateFailed(f"Error communicating with Sigenergy system: {exception}") from exception
        finally:
            # A timeout/cancellation otherwise stops only the child currently
            # awaited, leaving sibling polls running into the next update.
            # Cancel the read workflows and retrieve every result. The device
            # layer still lets each in-flight wire transaction finish safely.
            fetch_time = time.perf_counter() - start_time
            cleanup_started = time.perf_counter()
            try:
                for task in device_tasks:
                    if not task.done():
                        task.cancel()
                if device_tasks:
                    await asyncio.gather(*device_tasks, return_exceptions=True)
            finally:
                # Include failed attempts, and keep workflow cleanup separate
                # from the fetch deadline. Device times include broker waits;
                # they overlap and must not be summed as wire transaction time.
                self.latest_fetch_time = fetch_time
                self.largest_update_interval = max(self.largest_update_interval, fetch_time)
                if phase == "startup" and status == "success":
                    self.startup_fetch_time = fetch_time
                self.latest_update_timing = {
                    "phase": phase,
                    "status": status,
                    "timeout": timeout,
                    "fetch_time": fetch_time,
                    "cleanup_time": time.perf_counter() - cleanup_started,
                    "total_time": time.perf_counter() - start_time,
                    "devices": device_timings,
                }
                _LOGGER.debug("Sigenergy update timing: %s", self.latest_update_timing)

    async def _async_confirm_remote_ems_write(
        self, register_name: str, value: Union[int, float, str]
    ) -> None:
        """Confirm one EMS write using a fresh, serialized coordinator poll.

        async_request_refresh() may only queue a refresh during HA's cooldown.
        In HA 2026.10 async_refresh() waits for the existing refresh lock and
        performs a new poll, including entity notifications, before returning.
        Never accept an older in-flight poll or a matching cached value as
        confirmation, and never repeat the command if confirmation fails.
        """
        after_sequence = self._update_sequence
        try:
            async with async_timeout.timeout(60):
                await self.async_refresh()
        except TimeoutError as ex:
            raise SigenergyModbusError(
                f"Write to {register_name} was sent, but fresh readback timed out"
            ) from ex

        if (
            not self.last_update_success
            or self._last_successful_update_sequence <= after_sequence
        ):
            raise SigenergyModbusError(
                f"Write to {register_name} was sent, but no fresh readback is available"
            )
        actual = (self.data or {}).get("plant", {}).get(register_name)
        if actual != value:
            raise SigenergyModbusError(
                f"Write to {register_name} was sent, but fresh readback was "
                f"{actual!r}, expected {value!r}"
            )
        _LOGGER.debug(
            "Confirmed %s=%s by fresh readback in update %s",
            register_name, actual, self._last_successful_update_sequence,
        )

    async def async_write_parameter(
        self,
        device_type: str,
        device_identifier: Optional[str],
        register_name: str,
        value: Union[int, float, str]
    ) -> None:
        """Write a parameter; confirm Remote EMS commands before returning."""

        async def _write_and_refresh() -> None:
            try:
                await self.hub.async_write_parameter(
                    device_type=device_type,
                    device_identifier=device_identifier,
                    register_name=register_name,
                    value=value,
                )
                if device_type == "plant" and register_name in {
                    "plant_remote_ems_enable", "plant_remote_ems_control_mode"
                }:
                    await self._async_confirm_remote_ems_write(register_name, value)
                else:
                    await self.async_request_refresh()
            except SigenergyModbusError as ex:
                _LOGGER.error("Failed to complete parameter command %s to %s '%s': %s",
                              register_name, device_type, device_identifier or 'plant', ex)
                raise
            except Exception as ex:
                _LOGGER.error("Unexpected error writing parameter %s to %s '%s': %s",
                              register_name, device_type, device_identifier or 'plant', ex)
                raise

        def _consume_result(task: asyncio.Task[None]) -> None:
            # Errors are logged with their device/register context inside
            # the task, even if the caller has already been cancelled.
            if not task.cancelled():
                task.exception()

        # Keep the task tracked by the config entry after caller cancellation.
        task = self.hub.config_entry.async_create_task(
            self.hass, _write_and_refresh(), name=f"Sigenergy write {register_name}"
        )
        task.add_done_callback(_consume_result)

        # Cancelling the service caller must not cancel the write/refresh.
        # wait() leaves the tracked task running and avoids shield()'s
        # separate late-exception reports on Python 3.14.
        await asyncio.wait({task})
        task.result()

    def mark_sensors_initialized(self) -> None:
        """Mark that static sensors have been initialized and calculated sensors can run."""
        if self.data is not None:
            self.data["_sensors_initialized"] = True
            _LOGGER.debug("Static sensors marked as initialized - calculated sensors can now execute")
