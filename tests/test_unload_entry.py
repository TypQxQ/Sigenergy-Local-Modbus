"""Failed platform unload restores the retained hub, successful teardown drains."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from test_sigen_components import _load_sigen_module


def unload_entry():
    """Load the actual entry hook without requiring HA in regression jobs."""
    source = Path(__file__).resolve().parents[1] / "custom_components/sigen/__init__.py"
    function = next(node for node in ast.parse(source.read_text(encoding="utf-8")).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_unload_entry")
    namespace = {"DOMAIN": "sigen", "PLATFORMS": ("number", "sensor"),
                 "HomeAssistant": object, "ConfigEntry": object}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["async_unload_entry"]


class TestUnloadEntry(IsolatedAsyncioTestCase):
    def setUp(self):
        self.unload = unload_entry()
        self.hub = SimpleNamespace(_stopping=False, async_close=AsyncMock())
        self.entry = SimpleNamespace(entry_id="test")
        self.hass = SimpleNamespace(data={"sigen": {"test": {"hub": self.hub}}},
            config_entries=SimpleNamespace(async_unload_platforms=AsyncMock()))

    async def test_failed_platform_unload_restores_previous_flag_and_allows_writes(self):
        for result in (False, RuntimeError("platform failure"), asyncio.CancelledError()):
            with self.subTest(result=type(result).__name__):
                async def platform_unload(*_args):
                    self.assertTrue(self.hub._stopping)
                    if isinstance(result, BaseException):
                        raise result
                    return result
                self.hass.config_entries.async_unload_platforms.side_effect = platform_unload
                if isinstance(result, BaseException):
                    with self.assertRaises(type(result)):
                        await self.unload(self.hass, self.entry)
                else:
                    self.assertFalse(await self.unload(self.hass, self.entry))
                self.assertFalse(self.hub._stopping)
                self.assertIs(self.hub, self.hass.data["sigen"]["test"]["hub"])
                self.hub.async_close.assert_not_awaited()
                lifecycle = _load_sigen_module("write_lifecycle")
                group = SimpleNamespace(_command_epoch=0,
                    _write_allowed=lambda: not self.hub._stopping,
                    is_supported=lambda _key: True, _pending_requests=set(),
                    _on_write=lambda: None, _endpoint_poll=None)
                component = SimpleNamespace(write=AsyncMock())
                await lifecycle.WriteAdmission().write(group, component, "target", 1)
                component.write.assert_awaited_once()

    async def test_failed_unload_preserves_an_already_stopping_hub(self):
        self.hub._stopping = True
        self.hass.config_entries.async_unload_platforms.return_value = False
        self.assertFalse(await self.unload(self.hass, self.entry))
        self.assertTrue(self.hub._stopping)

    async def test_successful_teardown_keeps_hub_until_close_finishes(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def close():
            started.set()
            await finish.wait()
        self.hub.async_close.side_effect = close
        self.hass.config_entries.async_unload_platforms.return_value = True
        task = asyncio.create_task(self.unload(self.hass, self.entry))
        try:
            await asyncio.wait_for(started.wait(), 1)
            self.assertTrue(self.hub._stopping)
            self.assertIn("test", self.hass.data["sigen"])
            self.assertFalse(task.done())
        finally:
            finish.set()
        self.assertTrue(await task)
        self.assertNotIn("test", self.hass.data["sigen"])
        self.assertTrue(self.hub._stopping)

    async def test_interrupted_close_after_teardown_keeps_retained_hub_stopped(self):
        for error in (RuntimeError("drain failure"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                self.hub._stopping = False
                self.hass.config_entries.async_unload_platforms.return_value = True
                self.hub.async_close.side_effect = error
                with self.assertRaises(type(error)):
                    await self.unload(self.hass, self.entry)
                self.assertTrue(self.hub._stopping)
                self.assertIs(self.hub, self.hass.data["sigen"]["test"]["hub"])
