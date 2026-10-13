"""Regression tests for config_flow.py's connection-check timeout handling.

config_flow.py pulls in a large chunk of Home Assistant (voluptuous
selectors, entity/device registry helpers, the sensor platform, ...) that
isn't worth stubbing just to import the module directly. Instead, following
this repo's existing pattern (see test_energy_zero_guard.py), the single
method under test is extracted from the class by AST, compiled in isolation
with a minimal namespace, and exercised directly -- no Home Assistant
install required.
"""

from __future__ import annotations

import ast
import asyncio
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import MethodType
from unittest.mock import Mock

SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "config_flow.py"
)


class _FakeModbusError(Exception):
    """Stands in for modbus_connection.ModbusError for this isolated test."""


def _extract_method(class_name: str, method_name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name
    )


def _make_try_read_register(async_get_temporary_unit, logger=None):
    """Compile `_async_try_read_register` in isolation, bound to a fake self.

    `async_get_temporary_unit` is the only real collaborator this method
    talks to (besides asyncio itself and ModbusError), so it's the only
    thing a caller needs to fake to control the scenario under test.
    """
    func_def = _extract_method("SigenergyConfigFlow", "_async_try_read_register")
    module = ast.fix_missing_locations(ast.Module(body=[func_def], type_ignores=[]))
    namespace = {
        "asyncio": asyncio,
        "ModbusError": _FakeModbusError,
        "ModbusTcpParams": object,
        "async_get_temporary_unit": async_get_temporary_unit,
        "_LOGGER": logger or Mock(),
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)

    fake_self = Mock()
    fake_self.hass = object()
    return MethodType(namespace["_async_try_read_register"], fake_self)


class _Params:
    host = "10.0.0.5"
    port = 502


def _slow_unit_factory(delay: float, *, fail_with: Exception | None = None):
    """A fake async_get_temporary_unit whose read takes `delay` seconds."""
    started = asyncio.Event()
    finished = asyncio.Event()

    class _Unit:
        async def read_input_registers(self, address, count):
            started.set()
            await asyncio.sleep(delay)
            finished.set()
            if fail_with is not None:
                raise fail_with
            return [0] * count

    @asynccontextmanager
    async def fake_async_get_temporary_unit(hass, params, unit_id):
        yield _Unit()

    return fake_async_get_temporary_unit, started, finished


class TestAsyncTryReadRegisterTimeout(unittest.IsolatedAsyncioTestCase):
    """`timeout` must be enforced around the read, and a late failure must
    still be logged with this helper's usual context instead of becoming an
    unretrieved-task-exception warning.
    """

    async def test_short_timeout_fails_fast_against_a_slow_device(self) -> None:
        fake_unit, _started, _finished = _slow_unit_factory(delay=0.2)
        read = _make_try_read_register(fake_unit)

        start = time.monotonic()
        result = await read(_Params(), 1, 30003, timeout=0.05)
        elapsed = time.monotonic() - start

        self.assertIn("timed out", result.lower())
        self.assertLess(elapsed, 0.15, "timeout was not actually enforced")

    async def test_longer_timeout_allows_the_same_slow_read_to_succeed(self) -> None:
        fake_unit, _started, _finished = _slow_unit_factory(delay=0.05)
        read = _make_try_read_register(fake_unit)

        result = await read(_Params(), 1, 30003, timeout=2)

        self.assertEqual("", result)

    async def test_timed_out_read_is_not_cancelled_and_keeps_running(self) -> None:
        fake_unit, started, finished = _slow_unit_factory(delay=0.2)
        read = _make_try_read_register(fake_unit)

        await read(_Params(), 1, 30003, timeout=0.05)

        self.assertTrue(started.is_set())
        self.assertFalse(finished.is_set(), "read must not have been cancelled yet")
        await asyncio.wait_for(finished.wait(), timeout=1)

    async def test_late_failure_is_logged_with_register_context_not_dropped(self) -> None:
        logger = Mock()
        fake_unit, _started, finished = _slow_unit_factory(
            delay=0.05, fail_with=_FakeModbusError("late failure")
        )
        read = _make_try_read_register(fake_unit, logger=logger)

        result = await read(_Params(), 7, 30003, timeout=0.01)
        self.assertIn("timed out", result.lower())

        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)  # let the done callback run

        late_messages = [
            call.args[0] % call.args[1:]
            for call in logger.debug.call_args_list
            if "already elapsed" in call.args[0]
        ]
        self.assertEqual(1, len(late_messages))
        self.assertIn("30003", late_messages[0])
        self.assertIn("slave 7", late_messages[0])


if __name__ == "__main__":
    unittest.main()
