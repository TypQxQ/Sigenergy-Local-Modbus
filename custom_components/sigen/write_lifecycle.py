"""Local command admission; handoff deliberately precedes opaque broker I/O."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)


class CommandRejected(Exception):
    """Local command rejection, translated by the hub into an HA service error."""

    def __init__(self, message, key="command_invalidated"):
        super().__init__(message)
        self.translation_key = key


@dataclass
class Command:
    caller: asyncio.Task
    deadline: float
    phase: str = "local"
    worker: asyncio.Task | None = None
    refresh: Any = None
    refresh_requested: bool = False
    detached: bool = False
    on_handoff: Any = None
    generation: int | None = None
    acknowledged_at: float | None = None

    def request_refresh(self):
        if self.refresh is not None and not self.refresh_requested:
            self.refresh_requested = True
            self.refresh()


CURRENT_COMMAND: ContextVar[Command | None] = ContextVar("sigen_command", default=None)


class WriteAdmission:
    """Endpoint-local ownership, independent of service caller lifetime."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.local: set[asyncio.Task] = set()
        self.pending: set[asyncio.Task] = set()
        self.waiting = 0
        self.last_warning = float('-inf')
        self.counts = Counter()
        self.history = deque(maxlen=20)

    async def async_drain(self):
        while pending := {t for t in self.pending if not t.done()}:
            await asyncio.wait(pending)

    async def write(self, group, component, register, value):
        ctx = CURRENT_COMMAND.get() or Command(
            asyncio.current_task(), asyncio.get_running_loop().time() + 10)
        if self.waiting >= 16:
            self.counts["rejected_queue_full"] += 1
            raise CommandRejected("Sigenergy command queue is full; write was not sent", "command_queue_full")
        self.waiting += 1
        self.counts["admitted"] += 1
        epoch = getattr(group, "_command_epoch", 0)

        started = False

        async def run():
            nonlocal started
            started = True
            acquired = handed = False
            task = asyncio.current_task()
            outcome = "not_sent"
            handed_off_at = None
            try:
                try:
                    async with asyncio.timeout_at(ctx.deadline):
                        await self.lock.acquire()
                        acquired = True
                except TimeoutError as ex:
                    outcome = "expired_not_sent"
                    raise CommandRejected("Sigenergy write expired before handoff; not sent", "command_expired") from ex
                if ctx.caller.cancelling():
                    raise asyncio.CancelledError
                if asyncio.get_running_loop().time() >= ctx.deadline:
                    outcome = "expired_not_sent"
                    raise CommandRejected("Sigenergy write expired before handoff; not sent", "command_expired")
                if (epoch != getattr(group, "_command_epoch", 0)
                        or not group._write_allowed()
                        or group.is_supported(register) is False):
                    raise CommandRejected("Sigenergy write invalidated before handoff; not sent")
                ctx.phase = "handed_off"
                handed = True
                self.waiting -= 1
                if ctx.on_handoff is not None:
                    ctx.on_handoff()
                self.pending.add(task)
                group._pending_requests.add(task)
                group._on_write()
                outcome = "outcome_unknown"
                handed_off_at = datetime.now(timezone.utc).isoformat()
                try:
                    await component.write(register, value)
                except Exception as ex:
                    from modbus_connection import ModbusTimeoutError
                    from modbus_connection.exceptions import ModbusExceptionError
                    if isinstance(ex, ModbusExceptionError) and ex.exception_code not in (5, 6):
                        outcome = "device_rejected"
                    if isinstance(ex, ModbusTimeoutError) and group._endpoint_poll is not None:
                        await group._endpoint_poll.async_recycle_locked(component.modbus_unit)
                    raise
                ctx.phase = "acknowledged"
                ctx.acknowledged_at = asyncio.get_running_loop().time()
                outcome = "acknowledged"
            except asyncio.CancelledError:
                if not handed:
                    outcome = "cancelled_not_sent"
                raise
            finally:
                if not handed:
                    self.waiting -= 1
                else:
                    group._on_write()
                self.pending.discard(task)
                group._pending_requests.discard(task)
                if acquired:
                    self.lock.release()
                if outcome == "outcome_unknown" and time.monotonic() - self.last_warning >= 30:
                    self.last_warning = time.monotonic()
                    _LOGGER.warning("Write to %s has unknown outcome; no automatic retry", register)
                self.counts[outcome] += 1
                self.history.append({"register": register, "outcome": outcome,
                                     "handed_off_at": handed_off_at,
                                     "completed_at": datetime.now(timezone.utc).isoformat()})
                if outcome == "acknowledged" and (ctx.detached or ctx.caller.cancelling()):
                    ctx.request_refresh()

        task = asyncio.create_task(run(), name=f"Sigenergy write {register}")
        ctx.worker = task
        self.local.add(task)

        def done(t):
            if not started:
                self.waiting -= 1
                self.counts["cancelled_not_sent"] += 1
            if ctx.worker is t:
                ctx.worker = None
            self.local.discard(t)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(done)
        try:
            await asyncio.wait({task})
            return task.result()
        except asyncio.CancelledError:
            ctx.detached = True
            if ctx.phase == "local":
                task.cancel()
            elif ctx.phase == "acknowledged":
                ctx.request_refresh()
            raise
