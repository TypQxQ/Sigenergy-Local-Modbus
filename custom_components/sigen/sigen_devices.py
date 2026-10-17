"""Device wrappers over the Sigenergy modbus_connection Components.

Sigenergy ships different register sets across inverter/firmware variants,
so (like the old `modbus.py` hub) a device's exact register support is not
validated through live reads. `_ComponentGroup` restores cached capabilities
and discovers remaining fields incrementally, using strictly contiguous blocks
of at most 123 registers. Each block's failure is isolated from the others.
Declared fields stay intact so an unsupported field can become supported later;
the support map guards both polling and writes.

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
from collections import deque
from collections.abc import Coroutine
from copy import deepcopy
import hashlib
import json
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

# Keep incomplete read rounds in memory for recovery, never on disk. Older
# rounds are discarded so a long outage cannot revive arbitrarily old data.
_POLL_PROGRESS_MAX_AGE = 180.0
_MAX_DISCOVERY_BACKOFF = 300.0

IDENTITY_REGISTERS = {
    "inverter_model_type", "inverter_serial_number", "inverter_machine_firmware_version",
}


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
    inconclusive (e.g. a timeout) on that round or a later one. Only an
    illegal-address response can confirm a field unsupported.
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
        self.incremental = False
        self._support_changed = lambda: None
        self._probe_queue: deque[tuple[str, tuple[str, ...]]] = deque()
        self._queued_fields: set[tuple[str, str]] = set()
        self._checked_fields: set[tuple[str, str]] = set()
        self._current_probe = None
        self.discovery_prepared = False
        self.restored_discovery_jobs = 0
        self.discovery_retry_at = 0.0
        self.last_discovery_at = None
        self._discovery_failure_streak = 0
        self._rejected_blocks: dict[str, int] = {}
        self._prefetched: dict[str, dict[str, list[int]]] = {}
        self._prefetched_at: dict[str, float] = {}
        self.discovery_requests = 0
        self.discovery_failures = 0
        self.discovery_time = 0.0
        self.cache_status = "disabled"
        self.cache_age: float | None = None
        self._poll_lock = asyncio.Lock()
        self._read_round = None
        self._identity_snapshot = lambda: None
        self._confirm_endpoint_identity = None
        self.poll_resumes = 0
        self.poll_expired_rounds = 0
        self.poll_last_round_span = 0.0
        self._on_write = self.reset_poll_progress

    def fingerprint(self) -> str:
        # Field objects have address-bearing reprs, which change on restart.
        # Hash the declared wire/decoding schema, never object identity.
        attributes = ("address", "count", "signed", "scale", "offset", "word_order", "writable")
        fields = [(name, component.register_space, field_name, type(field).__name__,
                   {attr: getattr(field, attr, None) for attr in attributes})
                  for name, component in self._readable.items()
                  for field_name, field in component.declared_fields.items()]
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    def _set_support(self, component: str, field: str, value: bool | None) -> None:
        support = self._field_support[component]
        previous = support.get(field)
        if value is None:
            support.pop(field, None)
            self._checked_fields.discard((component, field))
        else:
            support[field] = value
            self._checked_fields.add((component, field))
        if previous is not value:
            self._support_changed()

    def enable_incremental(self, support: dict, changed, status: str) -> None:
        self.incremental = True
        self._support_changed = changed
        self.cache_status = status
        for name, component in self._readable.items():
            for field, value in support.get(name, {}).items():
                if field in component.declared_fields and type(value) is bool:
                    self._field_support[name].setdefault(field, value)

    def _enqueue(self, name: str, fields) -> None:
        fields = tuple(sorted(
            (field for field in fields if (name, field) not in self._queued_fields),
            key=lambda field: self._readable[name].declared_fields[field].address,
        ))
        if fields:
            self._queued_fields.update((name, field) for field in fields)
            self._probe_queue.append((name, fields))

    def queue_discovery(self) -> None:
        """Queue unknown blocks and one recheck of each cached negative."""
        for name, component in self._readable.items():
            for negative in (False, True):
                fields = {field_name: field for field_name, field in component.declared_fields.items()
                          if isinstance(field, RegisterField)
                          and (self._field_support[name].get(field_name) is False if negative
                               else field_name not in self._field_support[name])
                          and (name, field_name) not in self._checked_fields}
                self._queue_contiguous(name, fields)
        self.discovery_prepared = True
        self._support_changed()

    def _queue_contiguous(self, name, names):
        fields = {key: self._readable[name].declared_fields[key] for key in names}
        for start, count in _group_contiguous_blocks(
            [(field.address, field.count) for field in fields.values()]
        ):
            self._enqueue(name, [key for key, field in fields.items()
                                 if start <= field.address < start + count])

    def discovery_progress(self, session: str) -> dict:
        """Serialize capability work, excluding every measurement/raw word."""
        pending = list(self._probe_queue)
        if self._current_probe is not None:
            pending.insert(0, self._current_probe)
        remaining_backoff = max(0, self.discovery_retry_at - time.monotonic())
        return {"session": session, "pending": pending,
                "checked": sorted(self._checked_fields),
                "rejected_blocks": dict(self._rejected_blocks),
                "failure_streak": self._discovery_failure_streak,
                "last_request": (time.time() - max(0, time.monotonic() - self.last_discovery_at)
                                 if self.last_discovery_at is not None else None),
                "retry_after": time.time() + remaining_backoff if remaining_backoff else 0}

    def restore_discovery(self, progress: dict, session: str) -> None:
        """Resume split jobs on setup retry; new HA boots recheck negatives."""
        progress = progress if isinstance(progress, dict) else {}
        if progress.get("session") == session and isinstance(progress.get("checked"), list):
            for item in progress["checked"]:
                if (isinstance(item, (list, tuple)) and len(item) == 2
                        and all(isinstance(value, str) for value in item)
                        and item[0] in self._readable
                        and isinstance(self._readable[item[0]].declared_fields.get(item[1]), RegisterField)):
                    # Same-process pass bookkeeping can survive a reload even
                    # without verified identity. It does not restore negative
                    # capabilities; a new HA boot rechecks these fields.
                    self._checked_fields.add(tuple(item))
        rejected = progress.get("rejected_blocks", {})
        if isinstance(rejected, dict):
            self._rejected_blocks.update({key: min(value, 3) for key, value in rejected.items()
                if key in self._readable and type(value) is int and value >= 0})
        streak = progress.get("failure_streak", 0)
        self._discovery_failure_streak = min(streak, 4) if type(streak) is int and streak > 0 else 0
        retry_after = progress.get("retry_after", 0)
        if type(retry_after) in (int, float) and retry_after > time.time():
            self.discovery_retry_at = time.monotonic() + min(
                retry_after - time.time(), _MAX_DISCOVERY_BACKOFF
            )
        last_request = progress.get("last_request")
        if type(last_request) in (int, float) and last_request > 0:
            self.last_discovery_at = time.monotonic() - max(0, time.time() - last_request)
        pending = progress.get("pending", [])
        if isinstance(pending, list):
            for item in pending:
                if not (isinstance(item, (list, tuple)) and len(item) == 2
                        and isinstance(item[0], str) and item[0] in self._readable
                        and isinstance(item[1], (list, tuple))):
                    continue
                name, names = item
                fields = [field for field in names
                          if isinstance(field, str) and field in self._readable[name].declared_fields
                          and self._field_support[name].get(field) is not True
                          and (name, field) not in self._checked_fields]
                self._queue_contiguous(name, fields)
        self.restored_discovery_jobs = len(self._probe_queue)
        self.queue_discovery()

    async def async_bootstrap(self, register_names: set[str]) -> None:
        """Read a small startup set and reuse its live words in the first poll."""
        for name, component in self._readable.items():
            fields = {key: field for key, field in component.declared_fields.items()
                      if key in register_names and isinstance(field, RegisterField)}
            if not fields:
                continue
            started = time.monotonic()
            words, errors = await self._async_read_blocks(name, component, fields, probing=True)
            # Identity groups are tiny. Isolate a rejected identity field so
            # one optional field cannot prevent validating serial/firmware.
            if register_names & IDENTITY_REGISTERS:
                for field_name, error in errors.items():
                    if field_name not in IDENTITY_REGISTERS or not isinstance(error, IllegalDataAddressError):
                        continue
                    field_words, field_errors = await self._async_read_blocks(
                        name, component, {field_name: fields[field_name]}, probing=True
                    )
                    words.update(field_words)
                    if isinstance(field_errors.get(field_name), IllegalDataAddressError):
                        self._set_support(name, field_name, False)
            # PV child entities are created during platform setup. Do not
            # silently start with zero strings after an inconclusive read.
            count_error = errors.get("inverter_pv_string_count")
            if isinstance(count_error, IllegalDataAddressError):
                self._set_support(name, "inverter_pv_string_count", False)
            elif count_error is not None:
                raise count_error
            elif "inverter_pv_string_count" in fields:
                count_words = words.get("inverter_pv_string_count", ())
                if len(count_words) != fields["inverter_pv_string_count"].count:
                    raise ModbusError("Incomplete PV string count bootstrap response")
            self._prefetched.setdefault(name, {}).update(words)
            if words:
                self._prefetched_at[name] = min(self._prefetched_at.get(name, started), started)

    async def async_discover_one(self) -> bool:
        """Issue at most one request; fallback work goes into later intervals."""
        if not self._probe_queue or self._pending_requests:
            return False
        name, names = self._probe_queue.popleft()
        self._current_probe = (name, names)
        self._queued_fields.difference_update((name, field) for field in names)
        component = self._readable[name]
        fields = [component.declared_fields[field] for field in names]
        start = min(field.address for field in fields)
        count = max(field.address + field.count for field in fields) - start
        read = (component.modbus_unit.read_input_registers if component.register_space == "input"
                else component.modbus_unit.read_holding_registers)
        started = time.monotonic()
        self.last_discovery_at = started
        self.discovery_requests += 1
        try:
            words = await self._async_request(read(start, count))
            if len(words) != count:
                raise ModbusError("Incomplete discovery response")
        except IllegalDataAddressError:
            self._discovery_failure_streak = 0
            self.discovery_retry_at = 0
            self._rejected_blocks[name] = self._rejected_blocks.get(name, 0) + 1
            if len(names) == 1:
                self._set_support(name, names[0], False)
            elif (self._rejected_blocks[name] >= 3
                  or all(self._field_support[name].get(field) is False for field in names)):
                # An all-negative region is cheaper to check individually
                # than traversing an entirely failing binary tree.
                for field in names:
                    self._enqueue(name, (field,))
            else:
                middle = len(names) // 2
                self._enqueue(name, names[:middle])
                self._enqueue(name, names[middle:])
        except asyncio.CancelledError:
            self._enqueue(name, names)
            raise
        except ModbusError:
            self.discovery_failures += 1
            self._discovery_failure_streak += 1
            delay = min(PROBE_RETRY_DELAY * 2 ** min(self._discovery_failure_streak - 1, 3),
                        _MAX_DISCOVERY_BACKOFF)
            self.discovery_retry_at = time.monotonic() + delay
            self._enqueue(name, names)
            raise
        else:
            self._discovery_failure_streak = 0
            self.discovery_retry_at = 0
            self._rejected_blocks[name] = 0
            for field in names:
                self._set_support(name, field, True)
        finally:
            self._current_probe = None
            self.discovery_time += time.monotonic() - started
            self._support_changed()
        return True

    def discovery_diagnostics(self) -> dict:
        states = [self.is_supported(field) for component in self._readable.values()
                  for field in component.declared_fields]
        return {"cache": self.cache_status, "supported": states.count(True),
                "cache_age": self.cache_age,
                "unsupported": states.count(False), "unknown": states.count(None),
                "pending_requests": len(self._probe_queue),
                "restored_jobs": self.restored_discovery_jobs,
                "discovery_requests": self.discovery_requests,
                "discovery_failures": self.discovery_failures,
                "discovery_time": self.discovery_time,
                "retry_in": max(0, self.discovery_retry_at - time.monotonic()),
                "poll_recovery": {"resumes": self.poll_resumes,
                    "expired_rounds": self.poll_expired_rounds,
                    "last_round_span": self.poll_last_round_span,
                    "saved_fields": sum(len(words) for words in self._read_round["words"].values())
                                    if self._read_round else 0,
                    "round_age": time.monotonic() - self._read_round["started"]
                                 if self._read_round else None},
                "in_flight": len(self._pending_requests)}

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
        async with self._poll_lock:
            return await self._async_update_locked()

    def reset_poll_progress(self) -> None:
        """Invalidate pre-write/identity samples without losing capabilities."""
        self._read_round = None
        self._prefetched.clear()
        self._prefetched_at.clear()

    async def _async_confirm_poll_identity(self, progress) -> bool:
        """Confirm the whole endpoint, including groups without local identity."""
        if self._confirm_endpoint_identity is None:
            return False
        return await self._confirm_endpoint_identity(progress.get("identity"))

    async def _async_update_locked(self) -> dict[str, Any]:
        # A cancelled poll can leave a request queued in the shared broker,
        # as well as one already on the wire. Do not enqueue another round
        # behind those survivors. Cancelling this wait must leave them intact;
        # _async_request's completion callback consumes their late failures.
        pending = self._pending_requests.copy()
        if pending:
            await asyncio.wait(pending)

        # Bootstrap samples retain their original age, including across an
        # outage before the first ordinary poll. Never retime them as fresh.
        now = time.monotonic()
        for name in list(self._prefetched):
            if now - self._prefetched_at.get(name, -float("inf")) > _POLL_PROGRESS_MAX_AGE:
                self._prefetched.pop(name, None)
                self._prefetched_at.pop(name, None)
        progress = None
        if self.incremental:
            if (self._read_round is not None
                    and time.monotonic() - self._read_round["started"] > _POLL_PROGRESS_MAX_AGE):
                self._read_round = None
                self.poll_expired_rounds += 1
            if self._read_round is not None:
                saved_round = self._read_round
                try:
                    confirmed = await self._async_confirm_poll_identity(saved_round)
                except (ModbusError, asyncio.CancelledError):
                    self.reset_poll_progress()
                    raise
                if saved_round is not self._read_round:
                    raise ModbusError("Read round invalidated during identity validation")
                if not confirmed:
                    self.reset_poll_progress()
            if self._read_round is None:
                oldest = min([time.monotonic(), *self._prefetched_at.values()])
                self._read_round = {"started": oldest, "words": {}, "attempted": {},
                                    "identity": deepcopy(self._identity_snapshot())}
            else:
                self.poll_resumes += 1
            progress = self._read_round
        values: dict[str, Any] = {}
        for name, component in self._readable.items():
            if not self.incremental:
                await self._async_ensure_resolved(name, component)
            prefetched = self._prefetched.pop(name, {})
            self._prefetched_at.pop(name, None)
            await self._async_read_component(
                name, component, values, prefetched, progress=progress
            )
            if progress is not None and progress is not self._read_round:
                raise ModbusError("Read round invalidated by a write or identity change")
        if progress is not None:
            self.poll_last_round_span = time.monotonic() - progress["started"]
            if self.poll_last_round_span > _POLL_PROGRESS_MAX_AGE:
                self._read_round = None
                self.poll_expired_rounds += 1
                raise ModbusError("Read recovery expired; starting a fresh round")
        self._read_round = None
        return values

    async def async_write(self, register_name: str, value: Any) -> None:
        """Write a single register by name.

        Raises `AttributeError` for an unknown or confirmed-unsupported register.
        """
        # Honour per-field support without blocking unresolved or write-only
        # fields. Keep component declarations intact for later rediscovery.
        if self.is_supported(register_name) is False:
            raise AttributeError(f"unsupported register {register_name!r}")
        for component in (*self._readable.values(), *self._commands.values()):
            if register_name in component.declared_fields:
                self._on_write()

                async def write_and_invalidate():
                    try:
                        await component.write(register_name, value)
                    finally:
                        # This runs even when the caller was cancelled while
                        # the actual protected wire write was still pending.
                        self._on_write()

                await self._async_request(write_and_invalidate())
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
            # Keep declared fields intact so later discovery can expand them.
            # async_write() enforces the per-field support guard.

    async def _async_read_blocks(
        self, name: str, component: Component, fields: Mapping[str, RegisterField],
        *, probing: bool = False, progress: dict | None = None,
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
            affected = {key: field for key, field in fields.items()
                        if start <= field.address < start + count}

            async def read_and_commit(start=start, count=count, affected=affected):
                try:
                    block = await read(start, count)
                except ModbusError as ex:
                    if progress is self._read_round and progress is not None:
                        if not isinstance(ex, ModbusConnectionError):
                            progress["attempted"].setdefault(name, set()).update(affected)
                    raise
                # Commit in the protected task, so late successful answers
                # are retained even after the caller's deadline expires.
                for field_name, field in affected.items():
                    offset = field.address - start
                    field_words = block[offset:offset + field.count]
                    if progress is self._read_round and progress is not None:
                        progress["words"].setdefault(name, {})[field_name] = field_words
                        progress["attempted"].setdefault(name, set()).add(field_name)
                    if probing and self.incremental and len(field_words) == field.count:
                        self._set_support(name, field_name, True)
                return block

            try:
                block = await self._async_request(read_and_commit())
            except ModbusConnectionError:
                raise
            except ModbusError as ex:
                if self.incremental and not probing and isinstance(ex, IllegalDataAddressError):
                    affected = [field_name for field_name, field in fields.items()
                                if start <= field.address < start + count]
                    for field_name in affected:
                        self._set_support(name, field_name, False if len(affected) == 1 else None)
                    if len(affected) > 1:
                        self._enqueue(name, affected)
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
                    if probing and not self.incremental and len(words[field_name]) == field.count:
                        self._set_support(name, field_name, True)
        return words, errors

    async def _async_probe(self, name: str, component: Component) -> None:
        """Resolve as many of `component`'s still-unknown fields as possible.

        The first round reads every declared field as contiguous blocks:
        a block the device answers proves all of its fields, so most
        components resolve without any per-field probing. Fields still
        unknown after an illegal-address response are probed individually.
        Other failed blocks remain inconclusive until a later retry: spending
        their wire timeout twice can prevent the first refresh from finishing.
        Each field's result is recorded immediately and kept regardless of
        what happens to any other field. Inconclusive fields do not discard
        progress already made on the rest.

        Raises `ModbusError` if any field probed this round was inconclusive,
        so the caller retries the remaining unknown fields later.
        """
        support = self._field_support[name]
        deferred: dict[str, ModbusError] = {}
        if name not in self._bulk_probed:
            register_fields = {
                field_name: field
                for field_name, field in component.declared_fields.items()
                if isinstance(field, RegisterField) and field_name not in support
            }
            _words, errors = await self._async_read_blocks(
                name, component, register_fields, probing=True
            )
            # Even an entirely inconclusive bulk pass must move on to
            # individual probes after backoff, rather than repeat forever.
            self._bulk_probed.add(name)
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
        self, name: str, component: Component, values: dict[str, Any],
        prefetched: dict[str, list[int]] | None = None,
        *, progress: dict | None = None,
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
        words = (progress["words"].setdefault(name, {}) if progress is not None else {})
        words.update(prefetched or {})
        attempted = (progress["attempted"].setdefault(name, set()) if progress is not None else set())
        attempted.update(words)
        unread = {key: field for key, field in known_fields.items() if key not in attempted}
        fresh, _errors = await self._async_read_blocks(name, component, unread, progress=progress)
        words.update(fresh)
        for field_name in component.declared_fields:
            if field_name in words and field_name in known_fields:
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
