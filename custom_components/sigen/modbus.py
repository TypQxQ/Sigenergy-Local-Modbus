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
import asyncio
import time
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4
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
from .sigen_devices import IDENTITY_REGISTERS
from .support_cache import SupportCache, device_cache_key

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

# Entry setup retries share a pass; a complete HA restart starts a new pass
# over cached negatives. Nothing here is shared register support or live data.
_DISCOVERY_SESSION = uuid4().hex


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
        self.incremental_discovery = False
        self._cache = None
        self._bindings = []
        self._loaded_cache = {}
        self._identities = {}
        self._identity_rebuilds = {}
        self._identity_rebuild_locks = {}
        self._endpoint_identity = {}
        self._discovery_tasks = {}
        self._discovery_cursor = {}
        self._discovery_backoff = {}
        self._last_discovery = {}
        self._active_polls = 0
        self._active_writes = 0
        self._stopping = False
        self.bootstrap_time = None

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
        for _role, info, group in self._all_groups():
            endpoint = (info[CONF_HOST], info[CONF_PORT])
            group._on_write = lambda ep=endpoint: self._reset_poll_progress(ep)
            group._identity_snapshot = lambda ep=endpoint: self._endpoint_identity.get(ep, {})
            group._confirm_endpoint_identity = (
                lambda expected, ep=endpoint: self._async_confirm_endpoint_identity(ep, expected)
            )

    def _reset_poll_progress(self, endpoint):
        """Discard every pre-write sample on the affected physical link."""
        for _role, info, group in self._all_groups():
            if (info[CONF_HOST], info[CONF_PORT]) == endpoint:
                group.reset_poll_progress()

    async def _async_confirm_endpoint_identity(self, endpoint, expected):
        """Validate a saved round's endpoint before any sibling reuses it."""
        if not expected:
            return False
        identities = {}
        for name, info in self.inverter_connections.items():
            if (info[CONF_HOST], info[CONF_PORT]) != endpoint:
                continue
            group = self._inverters[name]._inverter_group
            data = {}
            for component_name, component in group._readable.items():
                fields = {key: field for key, field in component.declared_fields.items()
                          if key in IDENTITY_REGISTERS}
                if not fields:
                    continue
                words, errors = await group._async_read_blocks(
                    component_name, component, fields, probing=True
                )
                if errors:
                    return False
                for key, field in fields.items():
                    try:
                        if len(words.get(key, ())) != field.count:
                            return False
                        data[key] = field.decode(words[key])
                    except Exception:
                        return False
            identity = self._identity(data)
            if not identity:
                return False
            identities[str(info[CONF_SLAVE_ID])] = identity
        return (bool(identities) and identities == expected
                and self._endpoint_identity.get(endpoint) == expected)

    def _all_groups(self):
        plant = {**self.plant_connection,
                 CONF_SLAVE_ID: self.plant_connection.get(CONF_SLAVE_ID, self.plant_id)}
        yield "plant", plant, self._plant._group
        for name, inverter in self._inverters.items():
            info = self.inverter_connections[name]
            yield "inverter", info, inverter._inverter_group
            if inverter._dc_charger_group is not None:
                yield "dc_charger", info, inverter._dc_charger_group
        for name, charger in self._ac_chargers.items():
            yield "ac_charger", self.ac_charger_connections[name], charger._group

    @staticmethod
    def _identity(data):
        values = {key: str(data.get(key) or "").strip() for key in IDENTITY_REGISTERS}
        return values if all(values.values()) else {}

    @staticmethod
    def _bootstrap_names(role):
        return {
            "plant": {"plant_grid_sensor_active_power", "plant_ess_soc", "plant_running_state",
                      "plant_ess_power", "plant_sigen_photovoltaic_power", "plant_general_alarm1",
                      "plant_remote_ems_enable", "plant_remote_ems_control_mode"},
            "inverter": IDENTITY_REGISTERS | {"inverter_pv_string_count", "inverter_active_power",
                         "inverter_running_state", "inverter_ess_charge_discharge_power"},
            "dc_charger": {"dc_charger_running_state", "dc_charger_output_power",
                           "dc_charger_vehicle_soc"},
            "ac_charger": {"ac_charger_system_state"},
        }[role]

    def _checkpoint(self, key, endpoint, group):
        if self._cache is not None:
            loaded = self._loaded_cache.get(key, {})
            identity = self._endpoint_identity.get(endpoint, {})
            fingerprint = group.fingerprint()
            loaded_progress = {}
            if (loaded.get("fingerprint") == fingerprint
                    and (not identity or not loaded.get("identity") or loaded["identity"] == identity)):
                loaded_progress = loaded.get("discovery", {})
                if not identity:
                    loaded_progress = loaded.get("provisional_discovery", loaded_progress)
            discovery = (group.discovery_progress(_DISCOVERY_SESSION)
                         if group.discovery_prepared else loaded_progress)
            if (not identity and loaded.get("identity")
                    and loaded.get("fingerprint") == fingerprint):
                # Keep the verified generation intact until live identity is
                # available. Early/late replies belong to an unverified overlay,
                # whose positives alone can be reused on a subsequent retry.
                self._cache.update(key, fingerprint, loaded["identity"],
                                   loaded.get("support", {}), loaded.get("discovery", {}),
                                   provisional=group._field_support,
                                   provisional_discovery=discovery)
                return
            self._cache.update(key, fingerprint, identity,
                               group._field_support, discovery)

    async def async_initialize(self, cache=None) -> None:
        """Restore capabilities after live identity validation, then bootstrap."""
        started = time.monotonic()
        self._cache = cache or SupportCache(self.hass, self.config_entry.entry_id)
        await self._cache.async_load()
        self.incremental_discovery = True
        for role, info, group in self._all_groups():
            endpoint = (info[CONF_HOST], info[CONF_PORT])
            key = device_cache_key(role, info)
            self._bindings.append((key, endpoint, role, group))
            self._loaded_cache[key] = record = self._cache.snapshot(key)
            # Attach persistence before the very first identity request.
            # Until identity is live, only cached positives are safe to reuse.
            positives, _status = self._cache.restore(key, group.fingerprint(), {}, record=record)
            group.enable_incremental(
                positives, lambda k=key, e=endpoint, g=group: self._checkpoint(k, e, g),
                "validating",
            )
        for name, inverter in self._inverters.items():
            group = inverter._inverter_group
            # Identity and topology must be live before creating child devices.
            await group.async_bootstrap(IDENTITY_REGISTERS | {"inverter_pv_string_count"})
            data = {}
            for component_name, words in group._prefetched.items():
                component = group._readable[component_name]
                for field, raw in words.items():
                    try:
                        data[field] = component.declared_fields[field].decode(raw)
                    except Exception:
                        pass
            self._identities[name] = self._identity(data)
        self._refresh_endpoint_identities()
        for key, endpoint, role, group in self._bindings:
            restored, status = self._cache.restore(
                key, group.fingerprint(), self._endpoint_identity.get(endpoint, {}),
                record=self._loaded_cache[key],
            )
            # Provisional cached positives cannot survive an identity/schema
            # mismatch. Preserve only decisions made by this attempt's reads.
            group._field_support = {
                name: {field: value for field, value in states.items()
                       if (name, field) in group._checked_fields}
                for name, states in group._field_support.items()
            }
            group.enable_incremental(
                restored, lambda k=key, e=endpoint, g=group: self._checkpoint(k, e, g), status
            )
            group.cache_age = self._cache.age(key)
            names = {field for field in self._bootstrap_names(role)
                     if group.is_supported(field) is None}
            await group.async_bootstrap(names)
            progress = (self._loaded_cache[key].get("discovery", {})
                        if status in {"hit", "positive_only"} else {})
            if status == "positive_only":
                progress = self._loaded_cache[key].get("provisional_discovery", progress)
            group.restore_discovery(progress, _DISCOVERY_SESSION)
            self._discovery_backoff[endpoint] = max(
                self._discovery_backoff.get(endpoint, 0), group.discovery_retry_at
            )
            if group.last_discovery_at is not None:
                self._last_discovery[endpoint] = max(
                    self._last_discovery.get(endpoint, -float("inf")), group.last_discovery_at
                )
            self._checkpoint(key, endpoint, group)
        await self._cache.async_flush()
        self.bootstrap_time = time.monotonic() - started

    def _refresh_endpoint_identities(self):
        for _key, endpoint, _role, _group in self._bindings:
            identities = {
                str(info[CONF_SLAVE_ID]): self._identities.get(name, {})
                for name, info in self.inverter_connections.items()
                if (info[CONF_HOST], info[CONF_PORT]) == endpoint
            }
            self._endpoint_identity[endpoint] = (
                identities if identities and all(identities.values()) else {}
            )

    async def _async_observe_identity(self, name, data):
        identity = self._identity(data)
        if not identity or identity == self._identities.get(name):
            return
        info = self.inverter_connections[name]
        endpoint = (info[CONF_HOST], info[CONF_PORT])
        async with self._identity_rebuild_locks.setdefault(endpoint, asyncio.Lock()):
            # Inverters sharing an endpoint can report identity changes in the
            # same poll. Serialize invalidation as well as bootstrap retries.
            if identity != self._identities.get(name):
                await self._async_rebuild_identity(name, identity, endpoint)

    async def _async_rebuild_identity(self, name, identity, endpoint):
        discovery = self._discovery_tasks.get(endpoint)
        if discovery is not None and not discovery.done():
            discovery.cancel()
            await asyncio.gather(discovery, return_exceptions=True)
        async with self._async_pause_endpoint_polls(endpoint):
            await self._async_reset_identity(name, identity, endpoint)

    @asynccontextmanager
    async def _async_pause_endpoint_polls(self, endpoint):
        # Hold every group's poll lock, not merely a snapshot of its current
        # wire tasks: an active workflow can enqueue another block after one
        # request finishes. Also drain protected replies from cancelled polls.
        groups = [group for _key, ep, _role, group in self._bindings if ep == endpoint]
        async with AsyncExitStack() as stack:
            for group in groups:
                await stack.enter_async_context(group._poll_lock)
            pending = {task for group in groups for task in group._pending_requests}
            if pending:
                await asyncio.wait(pending)
            yield

    async def _async_reset_identity(self, name, identity, endpoint):
        # Old discovery callbacks must finish under the old identity stamp.
        self._identities[name] = identity
        self._refresh_endpoint_identities()
        self._discovery_backoff.pop(endpoint, None)
        rebuild = []
        for key, binding_endpoint, role, group in self._bindings:
            if binding_endpoint != endpoint:
                continue
            for states in group._field_support.values():
                states.clear()
            group._probe_queue.clear()
            group._queued_fields.clear()
            group._rejected_blocks.clear()
            group.reset_poll_progress()
            group._checked_fields.clear()
            group.discovery_retry_at = 0
            group._discovery_failure_streak = 0
            group.cache_status = "identity_changed"
            group.queue_discovery()
            self._checkpoint(key, endpoint, group)
            rebuild.append((key, role, group))
        # Invalidate and queue EVERY group before any cancellable reads. Keep
        # unfinished bootstrap work retryable even though identity is now new.
        self._identity_rebuilds[endpoint] = rebuild
        await self._async_resume_identity_rebuild_locked(endpoint)

    async def _async_resume_identity_rebuild(self, endpoint):
        async with self._identity_rebuild_locks.setdefault(endpoint, asyncio.Lock()):
            async with self._async_pause_endpoint_polls(endpoint):
                await self._async_resume_identity_rebuild_locked(endpoint)

    async def _async_resume_identity_rebuild_locked(self, endpoint):
        rebuild = self._identity_rebuilds.get(endpoint, [])
        for key, role, group in rebuild[:]:
            if group._pending_requests:
                await asyncio.wait(group._pending_requests.copy())
            names = {name for name in self._bootstrap_names(role)
                     if group.is_supported(name) is None}
            await group.async_bootstrap(names)
            self._checkpoint(key, endpoint, group)
            rebuild.remove((key, role, group))
        self._identity_rebuilds.pop(endpoint, None)

    def start_discovery(self, coordinator):
        self._coordinator = coordinator
        self.config_entry.async_on_unload(
            coordinator.async_add_listener(self.async_schedule_discovery)
        )
        self.async_schedule_discovery()

    def async_schedule_discovery(self):
        """At most one background request per endpoint after a successful poll."""
        if self._stopping or not self._coordinator.last_update_success:
            return
        for endpoint in self._endpoint_identity:
            if not any(ep == endpoint and group._probe_queue
                       for _key, ep, _role, group in self._bindings):
                continue
            interval = self._coordinator.update_interval.total_seconds()
            if time.monotonic() - self._last_discovery.get(endpoint, -float("inf")) < interval:
                continue
            task = self._discovery_tasks.get(endpoint)
            if task is not None and not task.done():
                continue
            if time.monotonic() < self._discovery_backoff.get(endpoint, 0):
                continue
            task = self.config_entry.async_create_background_task(
                self.hass, self._async_discover_endpoint(endpoint),
                name="Sigenergy capability discovery",
            )
            self._discovery_tasks[endpoint] = task

    async def _async_discover_endpoint(self, endpoint):
        rebuild_lock = self._identity_rebuild_locks.get(endpoint)
        if rebuild_lock is not None and rebuild_lock.locked():
            return
        groups = [group for _key, ep, _role, group in self._bindings if ep == endpoint]
        if self._active_polls or self._active_writes or any(g._pending_requests for g in groups):
            return
        start = self._discovery_cursor.get(endpoint, 0)
        for offset in range(len(groups)):
            index = (start + offset) % len(groups)
            if not groups[index]._probe_queue:
                continue
            self._discovery_cursor[endpoint] = (index + 1) % len(groups)
            self._last_discovery[endpoint] = time.monotonic()
            try:
                await groups[index].async_discover_one()
            except ModbusError:
                self._discovery_backoff[endpoint] = groups[index].discovery_retry_at
            return

    def discovery_diagnostics(self):
        return {
            "bootstrap_time": self.bootstrap_time,
            "cache_error": self._cache.last_error if self._cache else None,
            "devices": {f"{role}:{index}": group.discovery_diagnostics()
                        for index, (_key, _endpoint, role, group) in enumerate(self._bindings)},
        }

    async def async_close(self) -> None:
        """Stop workflows, drain protected transactions, checkpoint capabilities."""
        self._stopping = True
        for task in self._discovery_tasks.values():
            task.cancel()
        await asyncio.gather(*self._discovery_tasks.values(), return_exceptions=True)
        pending = [task for _role, _info, group in self._all_groups()
                   for task in group._pending_requests]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for _role, _info, group in self._all_groups():
            group.reset_poll_progress()
        if self._cache is not None:
            await self._cache.async_flush()

    def has_register(self, device_type: str, device_name: Optional[str], register_name: str) -> bool:
        """Distinguish unresolved registers from calculated/diagnostic keys."""
        group = None
        if device_type == DEVICE_TYPE_PLANT:
            group = self._plant._group
        elif device_type == DEVICE_TYPE_INVERTER:
            inverter = self._inverters.get(device_name or "")
            group = inverter._inverter_group if inverter else None
        elif device_type == DEVICE_TYPE_DC_CHARGER:
            inverter_name = (device_name or "").replace(" DC Charger", "").strip()
            inverter = self._inverters.get(inverter_name)
            group = inverter._dc_charger_group if inverter else None
        elif device_type == DEVICE_TYPE_AC_CHARGER:
            charger = self._ac_chargers.get(device_name or "")
            group = charger._group if charger else None
        return group.has_register(register_name) if group else False

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
        info = self.inverter_connections[inverter_name]
        endpoint = (info[CONF_HOST], info[CONF_PORT])
        if endpoint in self._identity_rebuilds:
            await self._async_resume_identity_rebuild(endpoint)
        data = await self._async_read(
            inverter.async_update_inverter(), f"inverter '{inverter_name}'"
        )
        if self.incremental_discovery:
            await self._async_observe_identity(inverter_name, data)
        return data

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

    async def _async_read(self, coro, description: str) -> Dict[str, Any]:
        """Await a device's `async_update()`, translating connection errors.

        Raises `SigenergyModbusError` if the link itself is down; any other
        per-register/per-component failure is already handled inside the
        device object, which returns a best-effort (possibly partial) dict.
        """
        self._active_polls += 1
        try:
            return await coro
        except ModbusConnectionError as ex:
            raise SigenergyModbusError(f"Connection error reading {description}: {ex}") from ex
        except ModbusError as ex:
            raise SigenergyModbusError(f"Modbus error reading {description}: {ex}") from ex
        finally:
            self._active_polls -= 1

    async def async_write_parameter(
        self, device_type: str, device_identifier: Optional[str],
        register_name: str, value: Union[int, float, str],
    ) -> None:
        self._active_writes += 1
        try:
            await self._async_write_parameter(device_type, device_identifier, register_name, value)
        finally:
            self._active_writes -= 1

    async def _async_write_parameter(
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
