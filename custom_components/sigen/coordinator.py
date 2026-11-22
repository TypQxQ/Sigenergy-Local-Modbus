"""Data update coordinator for Sigenergy ESS."""
from __future__ import annotations

import asyncio
import logging
import time
from asyncio import timeout as asyncio_timeout
from datetime import timedelta
from typing import Any, Dict, Optional, Union # Added Optional, Union

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed  # pylint: disable=syntax-error
from modbus_connection import ModbusConnectionError

from .write_lifecycle import Command, CURRENT_COMMAND
from .modbus import SigenergyModbusHub, SigenergyModbusError # Added SigenergyModbusError
from .const import CONF_INVERTER_HAS_DCCHARGER, DEFAULT_SCAN_INTERVAL, MIN_SCAN_INTERVAL
from .modbusregisterdefinitions import AC_CHARGER_RUNNING_INFO_REGISTERS

_LOGGER = logging.getLogger(__name__)

STARTUP_TIMEOUT = 180
UPDATE_TIMEOUT = 60
AC_CHARGER_PUBLISH_GRACE = 2.0
SLOW_POLL_WARNING_COUNT = 10


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
        self._consecutive_slow_polls = 0
        self.startup_fetch_time: float | None = None
        self.update_timeout_count = 0
        self.update_failure_count = 0
        self.partial_update_count = 0
        self.latest_update_timing: dict[str, Any] = {}
        self._degraded_devices: set[str] = set()
        self.pv_restore_states: dict[str, dict[str, Any]] = {}
        self.entity_name_translations: dict[str, str] | None = None
        self._verifiers: dict[asyncio.Future, tuple[int, str, Any]] = {}
        self._confirmation_task: asyncio.Task | None = None
        self._ems_commands = 0
        self._ems_generations: dict[str, int] = {}

        if scan_interval < MIN_SCAN_INTERVAL:
            scan_interval = DEFAULT_SCAN_INTERVAL
            logger.warning("Invalid scan_interval below %s, defaulting to %s",
                           MIN_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=timedelta(seconds=scan_interval),
        )

    def pv_restore_diagnostics(self) -> dict[str, Any]:
        """Copy restore status, computing pending ages without database work."""
        now = time.monotonic()
        result = {}
        for entity_id, state in self.pv_restore_states.items():
            row = {key: value for key, value in state.items() if not key.startswith("_")}
            if (started := state.get("_started_at")) is not None:
                row["total_elapsed"] = max(0.0, now - started)
            job_started = state.get("_job_started_at")
            row["recorder_job_age"] = max(0.0, now - job_started) if job_started is not None else None
            if (job := state.get("_recorder_job")) is not None:
                row.update(job.diagnostics(now))
            result[entity_id] = row
        return result

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via Modbus library."""
        self.hub.begin_poll_update()
        self._update_sequence += 1
        update_sequence = self._update_sequence
        device_tasks: set[asyncio.Task] = set()
        phase = "startup" if self._last_successful_update_sequence == 0 else "poll"
        timeout = STARTUP_TIMEOUT if phase == "startup" else UPDATE_TIMEOUT
        start_time = time.perf_counter()
        status = "cancelled"
        snapshot: dict[str, Any] | None = None
        early_data: dict[str, Any] | None = None
        can_publish_without_charger = False
        degraded_statuses = {"partial", "error"}
        plant_data: dict[str, Any] | None = None
        inverter_tasks: dict[str, asyncio.Task] = {}
        dc_tasks: dict[str, asyncio.Task] = {}
        ac_tasks: dict[str, asyncio.Task] = {}
        devices_at_deadline: set[str] = set()
        connection_failures: set[str] = set()
        device_timings: dict[str, dict[str, Any]] = {}
        previous_data = (self.data or {}) if self.last_update_success else {}

        async def timed_read(device: str, read, *args) -> Dict[str, Any]:
            started = time.perf_counter()
            device_status = "cancelled"
            quality: dict[str, Any] = {}
            try:
                result = await read(*args)
                quality = self.hub.get_poll_diagnostics(device)
                device_status = quality.get("status", "success")
                return result
            except Exception as error:
                device_status = "error"
                quality = {"error_type": type(error).__name__}
                if (
                    isinstance(error, SigenergyModbusError)
                    and isinstance(error.__cause__, ModbusConnectionError)
                ):
                    connection_failures.add(device)
                # Keep explicit cause types, never exception messages/payloads.
                # Bound traversal even for a malformed cyclic exception chain.
                seen = {id(error)}
                cause = error.__cause__
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    quality["cause_type"] = type(cause).__name__
                    cause = cause.__cause__
                raise
            finally:
                device_timings[device] = {
                    **quality,
                    "duration": time.perf_counter() - started,
                    "status": device_status,
                }

        def has_readings(*devices: dict[str, Any]) -> bool:
            return any(value is not None for device in devices for value in device.values())

        def completed_data(tasks: dict[str, asyncio.Task]) -> dict[str, Any]:
            return {
                name: (
                    task.result()
                    if task.done() and not task.cancelled() and task.exception() is None
                    else {}
                )
                for name, task in tasks.items()
            }

        def with_held_charger_telemetry(data: dict[str, Any], *, waiting=(), at_deadline=()):
            """Hold read-only EVAC telemetry through one missed poll per field."""
            held = {}
            for name, readings in data["ac_chargers"].items():
                previous = previous_data.get("ac_chargers", {}).get(name, {})
                if name in waiting:
                    # Early publication is provisional. Keep displaying an
                    # existing hold until the current read finishes, either way.
                    previous = previous | previous_data.get("_ac_charger_held", {}).get(name, {})
                if not previous:
                    continue
                fields = previous.keys() & AC_CHARGER_RUNNING_INFO_REGISTERS.keys()
                device = f"ac_charger:{name}"
                if (
                    name not in waiting and name not in at_deadline
                    and device not in connection_failures
                ):
                    # Use only the previous raw row for completed polls. A held
                    # value cannot carry forward through a second consecutive miss.
                    blocks = device_timings.get(device, {}).get("failed_blocks", ())
                    fields.intersection_update(
                        field for block in blocks
                        if (block.get("stage"), block.get("error_type")) in {
                            ("read", "ModbusTimeoutError"), ("skipped", "EndpointStopped")
                        }
                        for field in block.get("fields", ())
                    )
                values = {field: previous[field] for field in fields
                          if previous[field] is not None and readings.get(field) is None}
                if values:
                    held[name] = values
            # Controls still consume the raw charger rows.
            return {**data, "_ac_charger_held": held} if held else data

        try:
            async with asyncio_timeout(timeout):

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
                    except Exception:
                        inverter_data[name] = {}
                dc_charger_data: dict[str, Any] = {}
                for name, task in dc_tasks.items():
                    try:
                        dc_charger_data[name] = await task
                    except Exception:
                        dc_charger_data[name] = {}

                ac_tasks = {
                    name: asyncio.create_task(timed_read(
                        f"ac_charger:{name}", self.hub.async_read_ac_charger_data, name
                    ))
                    for name in self.hub.ac_charger_connections
                }
                device_tasks.update(ac_tasks.values())
                # Keep an empty row for missing charger data so calculated
                # consumption treats missing charger power as unknown.
                ac_charger_data: dict[str, Any] = {name: {} for name in ac_tasks}

                sensors_initialized = self.data.get("_sensors_initialized", False) if self.data else False
                snapshot = {
                    "plant": plant_data,
                    "inverters": inverter_data,
                    "ac_chargers": ac_charger_data,
                    "dc_chargers": dc_charger_data,
                    "_sensors_initialized": sensors_initialized,
                }
                can_publish_without_charger = (
                    bool(ac_tasks) and phase == "poll"
                    and any(value is not None for value in plant_data.values())
                )
                if can_publish_without_charger and self.last_update_success:
                    _, pending = await asyncio.wait(
                        ac_tasks.values(),
                        timeout=min(AC_CHARGER_PUBLISH_GRACE, self.update_interval.total_seconds()),
                    )
                    if pending:
                        # Publish the fresh plant/inverter snapshot without moving
                        # HA's refresh timer or declaring the poll complete.
                        early_data = with_held_charger_telemetry(
                            {**snapshot, "ac_chargers": completed_data(ac_tasks)},
                            waiting={name for name, task in ac_tasks.items() if task in pending},
                        )
                        self.data = early_data
                        self.async_update_listeners()

                for name, task in ac_tasks.items():
                    try:
                        ac_charger_data[name] = await task
                    except Exception:
                        pass  # Preserve the empty charger row.

                # Unknown/unsupported registers during discovery are expected.
                # A poll with actual read failures and no usable readings is
                # a total outage, not a successful partial update. Keep the old
                # snapshot until HA marks the coordinator unavailable.
                if (any(item["status"] in degraded_statuses for item in device_timings.values())
                        and not has_readings(plant_data, *inverter_data.values(),
                                             *dc_charger_data.values(), *ac_charger_data.values())):
                    raise UpdateFailed("No Sigenergy register readings succeeded")

                # A listener may have completed sensor initialization during
                # the early publication.
                if early_data is not None:
                    snapshot["_sensors_initialized"] = early_data["_sensors_initialized"]
                snapshot = with_held_charger_telemetry(snapshot)
                self.data = snapshot
                _LOGGER.debug("RBS-coordinator-106-coordinator-post Merged fetched, len: %s data: %s", len(self.data), self.data)

                # Return the updated, complete data structure
                status = ("partial" if any(item["status"] in degraded_statuses
                          for item in device_timings.values()) else "success")
                if status == "partial":
                    self.partial_update_count += 1
                self._last_successful_update_sequence = update_sequence
                return self.data
        except asyncio.TimeoutError as exception:
            self.update_timeout_count += 1
            if phase == "poll" and plant_data is not None:
                # Another endpoint can use up the rest of the deadline after
                # the plant read, for example with a timeout, reconnect and
                # timed-out re-read. Publish the readings that already arrived.
                # Propagating the deadline can already have cancelled the
                # device that the coordinator was awaiting. The finally block
                # stops pending workflows and lets wire requests finish safely.
                previous = early_data if early_data is not None else snapshot or self.data or {}
                # AC charger tasks start after the inverter and DC reads.
                unstarted_ac = [name for name in self.hub.ac_charger_connections if name not in ac_tasks]
                partial = {
                    "plant": plant_data,
                    "inverters": completed_data(inverter_tasks),
                    "ac_chargers": completed_data(ac_tasks) | {name: {} for name in unstarted_ac},
                    "dc_chargers": completed_data(dc_tasks),
                    "_sensors_initialized": previous.get("_sensors_initialized", False),
                }
                if has_readings(partial["plant"], *partial["inverters"].values(),
                                *partial["dc_chargers"].values(), *partial["ac_chargers"].values()):
                    devices_at_deadline = {
                        f"{kind}:{name}"
                        for kind, tasks in (("inverter", inverter_tasks), ("dc_charger", dc_tasks),
                                            ("ac_charger", ac_tasks))
                        for name, task in tasks.items()
                        if task.cancelled() or not task.done()
                    }
                    devices_at_deadline.update(f"ac_charger:{name}" for name in unstarted_ac)
                    snapshot = with_held_charger_telemetry(
                        partial, at_deadline={name for name in partial["ac_chargers"]
                                              if f"ac_charger:{name}" in devices_at_deadline},
                    )
                    status = "partial"
                    self.partial_update_count += 1
                    self._last_successful_update_sequence = update_sequence
                    return snapshot
            status = "timeout"
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
                if status in {"success", "partial"}:
                    self.hub.finish_poll_update()
                for device in devices_at_deadline:
                    device_timings.setdefault(device, {}).update(
                        status="error", error_type="TimeoutError",
                    )
                if status in {"success", "partial"}:
                    for name, values in (snapshot or {}).get("_ac_charger_held", {}).items():
                        device_timings[f"ac_charger:{name}"]["held_fields"] = sorted(values)

                # Include failed attempts, and keep workflow cleanup separate
                # from the fetch deadline. Device times include broker waits;
                # they overlap and must not be summed as wire transaction time.
                self.latest_fetch_time = fetch_time
                self.largest_update_interval = max(self.largest_update_interval, fetch_time)
                if phase == "poll" and status in {"success", "partial"}:
                    interval = self.update_interval.total_seconds()
                    if fetch_time > interval:
                        self._consecutive_slow_polls += 1
                        if self._consecutive_slow_polls == SLOW_POLL_WARNING_COUNT:
                            _LOGGER.warning(
                                "Sigenergy fetch time has exceeded the %.1f s polling interval for %s consecutive polls. "
                                "Consider a longer polling interval",
                                interval, SLOW_POLL_WARNING_COUNT,
                            )
                    else:
                        if self._consecutive_slow_polls >= SLOW_POLL_WARNING_COUNT:
                            _LOGGER.info("Sigenergy fetch time is back within the %.1f s polling interval", interval)
                        self._consecutive_slow_polls = 0
                if phase == "startup" and status in {"success", "partial"}:
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
                for device, timing in device_timings.items():
                    device_status = timing["status"]
                    if device_status in {"partial", "error"}:
                        if device not in self._degraded_devices:
                            _LOGGER.warning(
                                "Sigenergy read degraded for %s: status=%s, failed_fields=%s, "
                                "failed_blocks=%s, error_type=%s, cause_type=%s",
                                device, device_status, timing.get("failed_fields", []),
                                timing.get("failed_blocks", []), timing.get("error_type"),
                                timing.get("cause_type"),
                            )
                        self._degraded_devices.add(device)
                    elif device_status == "success" and device in self._degraded_devices:
                        self._degraded_devices.remove(device)
                        _LOGGER.info("Sigenergy read recovered for %s", device)
                _LOGGER.debug("Sigenergy update timing: %s", self.latest_update_timing)

    def _request_command_refresh(self):
        if not self.hub._stopping:
            # Detached workers still carry their command context. Preserve HA's
            # other context variables, but keep background polls command-free.
            token = CURRENT_COMMAND.set(None)
            try:
                self.hub.config_entry.async_create_background_task(
                    self.hass, self.async_request_refresh(),
                    name="Sigenergy command refresh", eager_start=True)
            finally:
                CURRENT_COMMAND.reset(token)

    async def _confirmation_pump(self):
        try:
            while self._verifiers and not self.hub._stopping:
                before = self._update_sequence
                await self.async_refresh()
                sequence = self._update_sequence
                if sequence <= before:
                    raise SigenergyModbusError("No fresh EMS poll available")
                data = dict((self.data or {}).get("plant", {}))
                for future, (barrier, register, expected) in list(self._verifiers.items()):
                    if sequence > barrier:
                        if not future.done():
                            future.set_result((self.last_update_success, data.get(register)))
                        self._verifiers.pop(future, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.hub._stopping:
                _LOGGER.warning("Shared EMS confirmation refresh failed")
        finally:
            for future in list(self._verifiers):
                if not future.done():
                    future.set_result((False, None))
            self._verifiers.clear()

    async def _async_confirm_remote_ems_write(self, register_name, value, acknowledged_at=None):
        barrier = self._update_sequence
        future = asyncio.get_running_loop().create_future()
        self._verifiers[future] = (barrier, register_name, value)
        if self._confirmation_task is None or self._confirmation_task.done():
            self._confirmation_task = self.hub.config_entry.async_create_background_task(
                self.hass, self._confirmation_pump(), name="Sigenergy EMS confirmation",
                eager_start=True)
        try:
            deadline = (acknowledged_at or asyncio.get_running_loop().time()) + 60
            async with asyncio_timeout(deadline - asyncio.get_running_loop().time()):
                await asyncio.wait({future})
                success, actual = future.result()
            if not success or actual != value:
                raise SigenergyModbusError(
                    f"Write acknowledged, but fresh readback of {register_name} was {actual!r}",
                    translation_domain="sigen", translation_key="command_unconfirmed")
        except TimeoutError as ex:
            raise SigenergyModbusError(
                f"Write acknowledged, but fresh readback of {register_name} timed out",
                translation_domain="sigen", translation_key="command_unconfirmed") from ex
        finally:
            self._verifiers.pop(future, None)
            future.cancel()

    async def async_write_parameter(
        self, device_type, device_identifier, register_name, value
    ) -> None:
        caller = asyncio.current_task()
        ctx = Command(caller, asyncio.get_running_loop().time() + 10,
                      refresh=self._request_command_refresh)
        ems = device_type == "plant" and register_name in {
            "plant_remote_ems_enable", "plant_remote_ems_control_mode"}
        if ems and self._ems_commands >= 4:
            raise SigenergyModbusError("Sigenergy EMS command capacity exceeded; not sent",
                                      translation_domain="sigen", translation_key="command_ems_busy")
        if ems:
            self._ems_commands += 1
        def mark_handoff():
            generation = self._ems_generations.get(register_name, 0) + 1
            self._ems_generations[register_name] = generation
            ctx.generation = generation
        if ems:
            ctx.on_handoff = mark_handoff
        try:
            token = CURRENT_COMMAND.set(ctx)
            try:
                await self.hub.async_write_parameter(
                    device_type=device_type, device_identifier=device_identifier,
                    register_name=register_name, value=value)
            finally:
                CURRENT_COMMAND.reset(token)
            if ems:
                if ctx.generation is None:
                    mark_handoff()
                await self._async_confirm_remote_ems_write(
                    register_name, value, ctx.acknowledged_at)
                if self._ems_generations.get(register_name) != ctx.generation:
                    raise SigenergyModbusError("Write acknowledged but superseded before confirmation",
                                              translation_domain="sigen",
                                              translation_key="command_superseded")
            else:
                ctx.request_refresh()
        except SigenergyModbusError:
            now = asyncio.get_running_loop().time()
            if now - getattr(self, "_last_command_error_log", float("-inf")) >= 30:
                self._last_command_error_log = now
                _LOGGER.error("Sigenergy command %s failed; no automatic retry", register_name)
            raise
        finally:
            if ems:
                def release(_=None):
                    self._ems_commands -= 1
                if ctx.worker is not None and not ctx.worker.done():
                    ctx.worker.add_done_callback(release)
                else:
                    release()

    def mark_sensors_initialized(self) -> None:
        """Mark that static sensors have been initialized and calculated sensors can run."""
        if self.data is not None:
            self.data["_sensors_initialized"] = True
            _LOGGER.debug("Static sensors marked as initialized - calculated sensors can now execute")
