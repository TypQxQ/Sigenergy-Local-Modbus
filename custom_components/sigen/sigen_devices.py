"""Device wrappers over the Sigenergy modbus_connection Components.

Sigenergy ships different register sets across inverter/firmware variants,
so (like the old `modbus.py` hub) a device's exact register support is not
known until it is probed once per connection. `_ComponentGroup` reproduces
the previous per-register probing (walking each declared field individually
once a bulk read fails) and then calls `restrict_fields()` to permanently
narrow the component to what the device actually answers -- the same end
state the old hub's `register_support` dict and hand-built read intervals
produced, built on the library's own planner instead.

A DC charger is not a separate Modbus unit: its registers live on the same
unit as its parent inverter, and were polled as a logically separate read by
the old hub/coordinator (two concurrent tasks sharing one connection). Each
`SigenergyInverter` therefore holds two independent `_ComponentGroup`s --
one for the inverter's own registers, one for its optional DC charger's --
so they keep resolving, updating and failing independently of each other.

Every wire-touching await below runs under `asyncio.shield()`. A write
triggered by an entity service call is awaited inside the caller's own task
(e.g. an automation's action sequence), and Home Assistant cancels that task
outright if the automation retriggers itself (`mode: restart`) while the
previous run is still in flight. Without shielding, that cancellation
reaches straight into the in-flight Modbus request: the device still answers
it, but nothing is listening for that transaction ID any more, which
tmodbus logs as "Received unexpected response" and recovers from by
dropping and reconnecting the link -- a connection blip (and a few seconds
of every entity on it going unavailable) that has nothing to do with the
device or the network. Shielding lets the request finish normally
regardless of whether the caller is still around to see the result.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Mapping

from modbus_connection import ModbusConnectionError, ModbusError, ModbusUnit
from modbus_connection.exceptions import IllegalDataAddressError
from modbus_connection.model import Component, RegisterField

from .sigen_components import (
    ACChargerCommands,
    ACChargerParameters,
    ACChargerRunningInfo,
    DCChargerCommands,
    DCChargerParameters,
    DCChargerRunningInfo,
    InverterCommands,
    InverterParameters,
    InverterRunningInfo,
    PlantCommands,
    PlantParameters,
    PlantPreheating,
    PlantRunningInfo,
)

_LOGGER = logging.getLogger(__name__)

#: Minimum time between register-support probe attempts for one component,
#: matching the old hub's `probe_retry_delay`.
PROBE_RETRY_DELAY = 60.0


class _ComponentGroup:
    """Probe, read and write one named set of components sharing a unit.

    `readable` components are probed and included in `async_update()`.
    `commands` components are write-only registers (see `sigen_components`)
    that are never updated, only written through.
    """

    def __init__(
        self, readable: Mapping[str, Component], commands: Mapping[str, Component]
    ) -> None:
        self._readable = readable
        self._commands = commands
        self._resolved: set[str] = set()
        self._last_probe_failure: dict[str, float] = {}

    def has_register(self, register_name: str) -> bool:
        """Whether this group declares `register_name`, read-only or writable."""
        return any(
            register_name in component.declared_fields
            for component in (*self._readable.values(), *self._commands.values())
        )

    def is_supported(self, register_name: str) -> bool | None:
        """True/False once resolved, else `None` (support not yet known)."""
        for name, component in self._readable.items():
            if register_name not in component.declared_fields:
                continue
            if name not in self._resolved:
                return None
            return register_name in component.resolved_fields
        for component in self._commands.values():
            if register_name in component.declared_fields:
                return True  # write-only registers are never probed
        return None

    async def async_update(self) -> dict[str, Any]:
        """Read every readable component and return `{register_name: value}`.

        An unresolved (not yet probed, or still backing off) component
        contributes no keys this cycle, same as a register whose support was
        still unknown under the old hub.

        Raises `ModbusConnectionError` if the link itself is down.
        """
        values: dict[str, Any] = {}
        for name, component in self._readable.items():
            await self._async_ensure_resolved(name, component)
            if name not in self._resolved:
                continue
            await self._async_read_component(name, component, values)
        return values

    async def async_write(self, register_name: str, value: Any) -> None:
        """Write a single register by name.

        Raises `AttributeError` if this group declares no such register.
        """
        for component in (*self._readable.values(), *self._commands.values()):
            if register_name in component.declared_fields:
                await asyncio.shield(component.write(register_name, value))
                return
        raise AttributeError(f"unknown register {register_name!r}")

    # -- probing --------------------------------------------------------------

    async def _async_ensure_resolved(self, name: str, component: Component) -> None:
        """Probe `component`'s register support once, with a retry backoff.

        Leaves the component alone if it is already resolved, or was probed
        too recently to retry. Never raises for an incomplete probe -- an
        unresolved component is simply skipped by the caller this cycle, the
        same outcome the old hub had while a register's support was unknown.

        Raises `ModbusConnectionError` if the link itself is down.
        """
        if name in self._resolved:
            return
        last_failure = self._last_probe_failure.get(name)
        if last_failure is not None and time.monotonic() - last_failure < PROBE_RETRY_DELAY:
            return
        try:
            await self._async_probe(component)
        except ModbusConnectionError:
            raise
        except ModbusError as ex:
            self._last_probe_failure[name] = time.monotonic()
            _LOGGER.debug(
                "Register probing for %s incomplete; retrying in %ss: %s",
                name,
                PROBE_RETRY_DELAY,
                ex,
            )
        else:
            self._resolved.add(name)
            self._last_probe_failure.pop(name, None)

    async def _async_probe(self, component: Component) -> None:
        """Resolve one component's register support, narrowing it if needed.

        Tries a normal bulk update first, since most components need no
        narrowing at all. Only on failure does it fall back to probing every
        declared field individually (mirroring the old hub's per-register
        probe) and call `restrict_fields()` once with the result.

        Raises `ModbusError` if any individual field's support could not be
        determined this round (e.g. a timeout), so the caller retries the
        whole component later rather than locking in an incomplete guess --
        `restrict_fields()` can only narrow a component, never widen it again.
        """
        try:
            await asyncio.shield(component.async_update(notify=False))
        except ModbusConnectionError:
            raise
        except ModbusError:
            pass
        else:
            return  # every declared field answered; nothing to restrict

        unit = component.modbus_unit
        read = (
            unit.read_input_registers
            if component.register_space == "input"
            else unit.read_holding_registers
        )
        supported: set[str] = set()
        for field_name, field in component.declared_fields.items():
            if not isinstance(field, RegisterField):
                supported.add(field_name)  # bit fields aren't probed; keep as-is
                continue
            try:
                await asyncio.shield(read(field.address, field.count))
            except IllegalDataAddressError:
                continue  # confirmed unsupported on this device
            except ModbusConnectionError:
                raise
            except ModbusError:
                raise  # inconclusive (e.g. timeout): retry the whole component later
            else:
                supported.add(field_name)
        component.restrict_fields(supported)

    async def _async_read_component(
        self, name: str, component: Component, values: dict[str, Any]
    ) -> None:
        """Update one component and scatter its decoded values into `values`.

        A component that fails to update (but whose link is still up) leaves
        its fields as `None` in `values` rather than failing the whole read,
        matching the old hub's per-register-block resilience.

        Raises `ModbusConnectionError` if the link itself is down.
        """
        try:
            await asyncio.shield(component.async_update())
        except ModbusConnectionError:
            raise
        except ModbusError as ex:
            _LOGGER.debug(
                "Reading %s failed; keeping previous values as unknown: %s", name, ex
            )
            for field_name in component.declared_fields:
                values.setdefault(field_name, None)
            return
        for field_name in component.declared_fields:
            values[field_name] = getattr(component, field_name)


class SigenergyPlant:
    """The plant (ESS) device: one unit, always present on a config entry."""

    def __init__(self, unit: ModbusUnit) -> None:
        self.running_info = PlantRunningInfo(unit)
        self.parameters = PlantParameters(unit)
        self.preheating = PlantPreheating(unit)
        self.commands = PlantCommands(unit)
        self._group = _ComponentGroup(
            {
                "running_info": self.running_info,
                "parameters": self.parameters,
                "preheating": self.preheating,
            },
            {"commands": self.commands},
        )

    async def async_update(self) -> dict[str, Any]:
        """Read all supported plant data."""
        return await self._group.async_update()

    async def async_write(self, register_name: str, value: Any) -> None:
        """Write a single plant register by name."""
        await self._group.async_write(register_name, value)

    def is_supported(self, register_name: str) -> bool | None:
        """True/False once resolved, else `None` (support not yet known)."""
        return self._group.is_supported(register_name)


class SigenergyInverter:
    """An inverter device, optionally with a co-located DC charger.

    The inverter and DC charger registers are read as two independent groups
    on the same unit, so a failure or probe backoff on one does not affect
    the other -- matching the old hub/coordinator polling them as two
    separate (but connection-sharing) reads.
    """

    def __init__(self, unit: ModbusUnit, *, has_dc_charger: bool) -> None:
        self.running_info = InverterRunningInfo(unit)
        self.parameters = InverterParameters(unit)
        self.commands = InverterCommands(unit)
        self._inverter_group = _ComponentGroup(
            {"running_info": self.running_info, "parameters": self.parameters},
            {"commands": self.commands},
        )

        self.dc_charger_running_info: DCChargerRunningInfo | None = None
        self.dc_charger_parameters: DCChargerParameters | None = None
        self.dc_charger_commands: DCChargerCommands | None = None
        self._dc_charger_group: _ComponentGroup | None = None
        if has_dc_charger:
            self.dc_charger_running_info = DCChargerRunningInfo(unit)
            self.dc_charger_parameters = DCChargerParameters(unit)
            self.dc_charger_commands = DCChargerCommands(unit)
            self._dc_charger_group = _ComponentGroup(
                {
                    "running_info": self.dc_charger_running_info,
                    "parameters": self.dc_charger_parameters,
                },
                {"commands": self.dc_charger_commands},
            )

    def _groups(self) -> tuple[_ComponentGroup, ...]:
        if self._dc_charger_group is None:
            return (self._inverter_group,)
        return (self._inverter_group, self._dc_charger_group)

    async def async_update_inverter(self) -> dict[str, Any]:
        """Read all supported inverter data (DC charger excluded)."""
        return await self._inverter_group.async_update()

    async def async_update_dc_charger(self) -> dict[str, Any]:
        """Read all supported DC charger data, or `{}` if this inverter has none."""
        if self._dc_charger_group is None:
            return {}
        return await self._dc_charger_group.async_update()

    async def async_write(self, register_name: str, value: Any) -> None:
        """Write a single inverter or DC-charger register by name.

        Register names never collide between the two groups, so the right
        one is found by name alone.

        Raises `AttributeError` if neither group declares `register_name`.
        """
        for group in self._groups():
            if group.has_register(register_name):
                await group.async_write(register_name, value)
                return
        raise AttributeError(f"unknown register {register_name!r}")

    def is_supported(self, register_name: str) -> bool | None:
        """True/False once resolved, else `None` (support not yet known)."""
        for group in self._groups():
            if group.has_register(register_name):
                return group.is_supported(register_name)
        return None


class SigenergyACCharger:
    """An AC charger device: one unit."""

    def __init__(self, unit: ModbusUnit) -> None:
        self.running_info = ACChargerRunningInfo(unit)
        self.parameters = ACChargerParameters(unit)
        self.commands = ACChargerCommands(unit)
        self._group = _ComponentGroup(
            {"running_info": self.running_info, "parameters": self.parameters},
            {"commands": self.commands},
        )

    async def async_update(self) -> dict[str, Any]:
        """Read all supported AC charger data."""
        return await self._group.async_update()

    async def async_write(self, register_name: str, value: Any) -> None:
        """Write a single AC charger register by name."""
        await self._group.async_write(register_name, value)

    def is_supported(self, register_name: str) -> bool | None:
        """True/False once resolved, else `None` (support not yet known)."""
        return self._group.is_supported(register_name)
