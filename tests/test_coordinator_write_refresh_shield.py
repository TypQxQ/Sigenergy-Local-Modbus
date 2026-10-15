"""Regression test for coordinator.py's write-triggered refresh shielding.

coordinator.py subclasses Home Assistant's DataUpdateCoordinator, which isn't
worth stubbing just to exercise this one method. Following this repo's
existing pattern (see test_energy_zero_guard.py), async_write_parameter is
extracted from the class by AST and exercised in isolation against fake
hub/refresh collaborators -- no Home Assistant install required.
"""

from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from types import MethodType
from unittest.mock import Mock

SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "coordinator.py"
)


class _FakeSigenergyModbusError(Exception):
    """Stands in for modbus.py's SigenergyModbusError for this isolated test."""


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


def _make_write_parameter(*, write_delay: float = 0.0, refresh_delay: float = 0.0, logger=None):
    """Compile `async_write_parameter` in isolation, bound to a fake self
    whose hub.async_write_parameter / async_request_refresh are fakes that
    report when they start and finish, so a test can observe whether
    cancelling the caller actually aborted them partway through.
    """
    func_def = _extract_method("SigenergyDataUpdateCoordinator", "async_write_parameter")
    module = ast.fix_missing_locations(ast.Module(body=[func_def], type_ignores=[]))
    namespace = {
        "asyncio": asyncio,
        "SigenergyModbusError": _FakeSigenergyModbusError,
        "_LOGGER": logger if logger is not None else Mock(),
        "Optional": object,
        "Union": object,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)

    events = {
        "write_started": asyncio.Event(),
        "write_finished": asyncio.Event(),
        "refresh_started": asyncio.Event(),
        "refresh_finished": asyncio.Event(),
    }

    async def fake_write_parameter(**kwargs):
        events["write_started"].set()
        await asyncio.sleep(write_delay)
        events["write_finished"].set()

    async def fake_request_refresh():
        events["refresh_started"].set()
        await asyncio.sleep(refresh_delay)
        events["refresh_finished"].set()

    fake_self = Mock()
    fake_self.hub.async_write_parameter = fake_write_parameter
    fake_self.async_request_refresh = fake_request_refresh

    def fake_create_task(hass, target, name=None):
        return asyncio.create_task(target, name=name)

    fake_self.hub.config_entry.async_create_task = fake_create_task

    bound = MethodType(namespace["async_write_parameter"], fake_self)
    return bound, events


class TestCoordinatorWriteRefreshShield(unittest.IsolatedAsyncioTestCase):
    """Cancelling the caller must not abort the write or the refresh it
    triggers partway through.
    """

    async def test_happy_path_runs_write_then_refresh_in_order(self) -> None:
        write_parameter, events = _make_write_parameter()

        await write_parameter("plant", None, "plant_ess_soc", 1)

        self.assertTrue(events["write_finished"].is_set())
        self.assertTrue(events["refresh_finished"].is_set())

    async def test_cancelling_the_caller_mid_write_does_not_abort_the_write(self) -> None:
        write_parameter, events = _make_write_parameter(write_delay=0.2)

        task = asyncio.ensure_future(
            write_parameter("plant", None, "plant_ess_soc", 1)
        )
        await asyncio.wait_for(events["write_started"].wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertFalse(
            events["write_finished"].is_set(),
            "test setup issue: write finished before cancellation could be observed",
        )
        await asyncio.wait_for(events["write_finished"].wait(), timeout=1)

    async def test_cancelling_the_caller_mid_refresh_does_not_abort_the_refresh(self) -> None:
        write_parameter, events = _make_write_parameter(refresh_delay=0.2)

        task = asyncio.ensure_future(
            write_parameter("plant", None, "plant_ess_soc", 1)
        )
        await asyncio.wait_for(events["refresh_started"].wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertFalse(
            events["refresh_finished"].is_set(),
            "test setup issue: refresh finished before cancellation could be observed",
        )
        await asyncio.wait_for(events["refresh_finished"].wait(), timeout=1)

    async def test_hub_error_is_logged_and_reraised(self) -> None:
        logger = Mock()
        write_parameter, _events = _make_write_parameter(logger=logger)
        write_parameter.__func__  # sanity: bound method exists

        async def failing_write(**kwargs):
            raise _FakeSigenergyModbusError("boom")

        write_parameter.__self__.hub.async_write_parameter = failing_write

        with self.assertRaises(_FakeSigenergyModbusError):
            await write_parameter("plant", None, "plant_ess_soc", 1)
        logger.error.assert_called_once()

    async def _assert_late_error_is_logged(self, stage: str, error: Exception) -> None:
        logger = Mock()
        write_parameter, _events = _make_write_parameter(logger=logger)
        loop = asyncio.get_running_loop()
        unhandled_errors = []
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled_errors.append(context))
        self.addCleanup(loop.set_exception_handler, original_handler)
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def fail_after_cancellation(*args, **kwargs):
            started.set()
            try:
                await release.wait()
                raise error
            finally:
                finished.set()

        if stage == "write":
            write_parameter.__self__.hub.async_write_parameter = fail_after_cancellation
        else:
            write_parameter.__self__.async_request_refresh = fail_after_cancellation

        caller = asyncio.create_task(
            write_parameter("inverter", "Sigen Inverter", "inverter_start_stop", 1)
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
        finally:
            release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)  # allow the shield's completion callback to run

        logger.error.assert_called_once()
        message = logger.error.call_args.args[0] % logger.error.call_args.args[1:]
        self.assertIn("inverter_start_stop", message)
        self.assertIn("Sigen Inverter", message)
        self.assertIn(str(error), message)
        self.assertEqual([], unhandled_errors)

    async def test_late_write_error_is_logged_after_caller_cancellation(self) -> None:
        await self._assert_late_error_is_logged("write", _FakeSigenergyModbusError("late write"))

    async def test_late_refresh_error_is_logged_after_caller_cancellation(self) -> None:
        await self._assert_late_error_is_logged("refresh", RuntimeError("late refresh"))


if __name__ == "__main__":
    unittest.main()
