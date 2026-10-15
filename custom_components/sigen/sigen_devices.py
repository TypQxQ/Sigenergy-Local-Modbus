"""Device wrappers over the Sigenergy modbus_connection Components.

Sigenergy ships different register sets across inverter/firmware variants,
so (like the old `modbus.py` hub) a device's exact register support is not
known until it is probed once per connection. `_ComponentGroup` reproduces
the previous probing (contiguous blocks first, then each still-unknown field
individually) and the old hub's read intervals: strictly contiguous blocks
of at most 123 registers, each block's failure isolated from the others.
Once every field of a component has an answer, `restrict_fields()` narrows
it so a write to an unsupported register is refused.

A DC charger is not a separate Modbus unit: its registers live on the same
unit as its parent inverter, and were polled as a logically separate read by
the old hub/coordinator (two concurrent tasks sharing one connection). Each
`SigenergyInverter` therefore holds two independent `_ComponentGroup`s --
one for the inverter's own registers, one for its optional DC charger's --
so they keep resolving, updating and failing independently of each other.

Every wire-touching await below uses a tracked task and `asyncio.wait()`. A write
triggered by an entity service call is awaited inside the caller's own task
(e.g. an automation's action sequence), and Home Assistant cancels that task
outright if the automation retriggers itself (`mode: restart`) while the
previous run is still in flight. Without shielding, that cancellation
reaches straight into the in-flight Modbus request: the device still answers
it, but nothing is listening for that transaction ID any more, which
tmodbus logs as "Received unexpected response" and recovers from by
dropping and reconnecting the link -- a connection blip (and a few seconds
of every entity on it going unavailable) that has nothing to do with the
device or the network. The request finishes and its result is consumed
regardless of whether the caller is still around. Unlike `asyncio.shield()`,
this does not report late errors again through the loop on Python 3.14.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
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

#: Modbus per-request register ceiling most gateways honour safely.
_MAX_BLOCK_SPAN = 123


def _group_contiguous_blocks(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Group sorted `(address, count)` spans into strictly-contiguous blocks.

    Mirrors the old hub's interval-building exactly: two spans merge only
    when there is no gap between them, capped at a safe per-request size, so
    a batched read can never cross into an address this component doesn't
    declare.
    """
    ordered = sorted(set(spans))
    if not ordered:
        return []
    blocks: list[tuple[int, int]] = []
    start, end = ordered[0][0], ordered[0][0] + ordered[0][1]
    for address, count in ordered[1:]:
        if address == end and (end - start + count) <= _MAX_BLOCK_SPAN:
            end += count
        else:
            blocks.append((start, end - start))
            start, end = address, address + count
    blocks.append((start, end - start))
    return blocks


class _ComponentGroup:
    """Probe, read and write one named set of components sharing a unit.

    `readable` components are probed and included in `async_update()`.
    `commands` components are write-only registers (see `sigen_components`)
    that are never updated, only written through.

    Register support is tracked per field, not per component, and persists
    across probe rounds: a field confirmed supported or unsupported on one
    round stays known even if another field on the same component is still
    inconclusive (e.g. a timeout) on that round or a later one. A component
    is only handed to `restrict_fields()` -- a one-shot, irreversible
    narrowing -- once every one of its fields has a known answer.
    """

    def __init__(
        self, readable: Mapping[str, Component], commands: Mapping[str, Component]
    ) -> None:
        self._readable = readable
        self._commands = commands
        self._field_support: dict[str, dict[str, bool]] = {name: {} for name in readable}
        self._bulk_probed: set[str] = set()
        self._last_probe_failure: dict[str, float] = {}
        self._pending_requests: set[asyncio.Task[Any]] = set()

    async def _async_request(self, operation: Coroutine[Any, Any, Any]) -> Any:
        """Let a broker request finish even if its caller is cancelled."""
        task = asyncio.create_task(operation)
        self._pending_requests.add(task)

        def _consume_result(completed: asyncio.Task[Any]) -> None:
            self._pending_requests.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(_consume_result)
        await asyncio.wait({task})
        return task.result()

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
            return self._field_support[name].get(register_name)
        for component in self._commands.values():
            if register_name in component.declared_fields:
                return True  # write-only registers are never probed
        return None

    async def async_update(self) -> dict[str, Any]:
        """Read every readable component and return `{register_name: value}`.

        A field whose support is still unknown contributes no key this
        cycle, same as a register whose support was still unknown under the
        old hub.

        Raises `ModbusConnectionError` if the link itself is down.
        """
        # A cancelled poll can leave a request queued in the shared broker,
        # as well as one already on the wire. Do not enqueue another round
        # behind those survivors. Cancelling this wait must leave them intact;
        # _async_request's completion callback consumes their late failures.
        pending = self._pending_requests.copy()
        if pending:
            await asyncio.wait(pending)

        values: dict[str, Any] = {}
        for name, component in self._readable.items():
            await self._async_ensure_resolved(name, component)
            await self._async_read_component(name, component, values)
        return values

    async def async_write(self, register_name: str, value: Any) -> None:
        """Write a single register by name.

        Raises `AttributeError` for an unknown or confirmed-unsupported register.
        """
        # A sibling can remain inconclusive indefinitely, delaying the
        # component's one-shot restrict_fields(). Honour what is already
        # known about this field without blocking unresolved or write-only ones.
        if self.is_supported(register_name) is False:
            raise AttributeError(f"unsupported register {register_name!r}")
        for component in (*self._readable.values(), *self._commands.values()):
            if register_name in component.declared_fields:
                await self._async_request(component.write(register_name, value))
                return
        raise AttributeError(f"unknown register {register_name!r}")

    # -- probing --------------------------------------------------------------

    def _is_fully_resolved(self, name: str, component: Component) -> bool:
        support = self._field_support[name]
        return all(field_name in support for field_name in component.declared_fields)

    async def _async_ensure_resolved(self, name: str, component: Component) -> None:
        """Probe `component`'s still-unknown fields once, with a retry backoff.

        Leaves the component alone if every field is already resolved, or it
        was probed too recently to retry. Never raises for an incomplete
        probe -- a field whose support is still unknown is simply skipped by
        the caller this cycle, the same outcome the old hub had.

        Raises `ModbusConnectionError` if the link itself is down.
        """
        if self._is_fully_resolved(name, component):
            return
        last_failure = self._last_probe_failure.get(name)
        if last_failure is not None and time.monotonic() - last_failure < PROBE_RETRY_DELAY:
            return
        try:
            await self._async_probe(name, component)
        except asyncio.CancelledError:
            # The coordinator's deadline can cancel probing before the wire
            # timeout is delivered. Back off here too, so the next poll reads
            # known fields instead of immediately repeating the slow probe.
            self._last_probe_failure[name] = time.monotonic()
            raise
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
            self._last_probe_failure.pop(name, None)
            if self._is_fully_resolved(name, component):
                supported = {
                    field_name
                    for field_name, ok in self._field_support[name].items()
                    if ok
                }
                component.restrict_fields(supported)

    async def _async_read_blocks(
        self, name: str, component: Component, fields: Mapping[str, RegisterField]
    ) -> tuple[dict[str, list[int]], dict[str, ModbusError]]:
        """Read contiguous blocks; return words and failed-block errors per field.

        Each block's failure is isolated: a field is missing from the result
        when its block failed to read (but the link is still up).

        Raises `ModbusConnectionError` if the link itself is down.
        """
        unit = component.modbus_unit
        read = (
            unit.read_input_registers
            if component.register_space == "input"
            else unit.read_holding_registers
        )
        words: dict[str, list[int]] = {}
        errors: dict[str, ModbusError] = {}
        for start, count in _group_contiguous_blocks(
            [(field.address, field.count) for field in fields.values()]
        ):
            try:
                block = await self._async_request(read(start, count))
            except ModbusConnectionError:
                raise
            except ModbusError as ex:
                errors.update(
                    (field_name, ex)
                    for field_name, field in fields.items()
                    if start <= field.address < start + count
                )
                _LOGGER.debug(
                    "Reading %s block at address %s (count %s) failed: %s",
                    name, start, count, ex,
                )
                continue
            for field_name, field in fields.items():
                if start <= field.address < start + count:
                    offset = field.address - start
                    words[field_name] = block[offset : offset + field.count]
        return words, errors

    async def _async_probe(self, name: str, component: Component) -> None:
        """Resolve as many of `component`'s still-unknown fields as possible.

        The first round reads every declared field as contiguous blocks:
        a block the device answers proves all of its fields, so most
        components resolve without any per-field probing. Fields still
        unknown after an illegal-address response are probed individually.
        Other failed blocks remain inconclusive until a later retry: spending
        their wire timeout twice can prevent the first refresh from finishing.
        A field's result (supported or confirmed
        unsupported) is recorded immediately and kept regardless of what
        happens to any other field -- `restrict_fields()` is applied by the
        caller only once every field has an answer, so one inconclusive
        field (e.g. a timeout) delays locking the component in without
        losing progress already made on the rest.

        Raises `ModbusError` if any field probed this round was inconclusive,
        so the caller retries the remaining unknown fields later.
        """
        support = self._field_support[name]
        deferred: dict[str, ModbusError] = {}
        if not support and name not in self._bulk_probed:
            register_fields = {
                field_name: field
                for field_name, field in component.declared_fields.items()
                if isinstance(field, RegisterField)
            }
            words, errors = await self._async_read_blocks(
                name, component, register_fields
            )
            # Even an entirely inconclusive bulk pass must move on to
            # individual probes after backoff, rather than repeat forever.
            self._bulk_probed.add(name)
            for field_name in words:
                support[field_name] = True
            deferred = {
                field_name: error for field_name, error in errors.items()
                if not isinstance(error, IllegalDataAddressError)
            }

        unit = component.modbus_unit
        read = (
            unit.read_input_registers
            if component.register_space == "input"
            else unit.read_holding_registers
        )
        inconclusive: ModbusError | None = next(iter(deferred.values()), None)
        for field_name, field in component.declared_fields.items():
            if field_name in support or field_name in deferred:
                continue
            if not isinstance(field, RegisterField):
                support[field_name] = True  # bit fields aren't probed; keep as-is
                continue
            try:
                await self._async_request(read(field.address, field.count))
            except IllegalDataAddressError:
                support[field_name] = False  # confirmed unsupported on this device
            except ModbusConnectionError:
                raise
            except ModbusError as ex:
                # Inconclusive (e.g. a timeout): leave unresolved and keep
                # probing the rest of the fields this round.
                inconclusive = ex
            else:
                support[field_name] = True
        if inconclusive is not None:
            raise inconclusive

    async def _async_read_component(
        self, name: str, component: Component, values: dict[str, Any]
    ) -> None:
        """Scatter `component`'s decoded values into `values`.

        Reads every currently-known-supported field as a batch of strictly
        contiguous blocks (same grouping regardless of whether the component
        is fully resolved yet), with each block's failure isolated from the
        others. `Component.async_update()` is not used: it reads every
        planned block before decoding any of them, so one failing block
        would blank the whole component. This matches the old hub's
        per-interval resilience, and also means a persistently-unresolved
        field never costs more than its own block on every poll.

        A field whose support is still unknown, whose block failed to read,
        or whose value failed to decode is left as `None` rather than failing
        the whole read. Decoder failures do not change register support.

        Raises `ModbusConnectionError` if the link itself is down.
        """
        support = self._field_support[name]
        known_fields = {
            field_name: field
            for field_name, field in component.declared_fields.items()
            if support.get(field_name) and isinstance(field, RegisterField)
        }
        words, _errors = await self._async_read_blocks(name, component, known_fields)
        for field_name in component.declared_fields:
            if field_name in words:
                field = known_fields[field_name]
                try:
                    if len(words[field_name]) != field.count:
                        raise ValueError("Incomplete register field")
                    values[field_name] = field.decode(words[field_name])
                except Exception as ex:
                    values[field_name] = None
                    # Decoder messages can contain serials or raw payloads.
                    _LOGGER.debug(
                        "Decoding %s.%s at address %s failed (%s)",
                        name, field_name, field.address, type(ex).__name__,
                    )
            else:
                values.setdefault(field_name, None)


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

        self.dc_charger_running_info: Component | None = None
        self.dc_charger_parameters: Component | None = None
        self.dc_charger_commands: Component | None = None
        self._dc_charger_group: _ComponentGroup | None = None
        if has_dc_charger:
            running_info = DCChargerRunningInfo(unit)
            parameters = DCChargerParameters(unit)
            commands = DCChargerCommands(unit)
            self.dc_charger_running_info = running_info
            self.dc_charger_parameters = parameters
            self.dc_charger_commands = commands
            self._dc_charger_group = _ComponentGroup(
                {"running_info": running_info, "parameters": parameters},
                {"commands": commands},
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
