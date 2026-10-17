"""Capability persistence is isolated by hardware, schema and config entry."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import unittest

from test_modbus_hub import _load_sigen_module


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.pending = None
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    def async_delay_save(self, factory, delay):
        self.pending = factory

    async def async_save(self, data):
        self.saves += 1
        self.data = deepcopy(data)
        self.pending = None


class TestSupportCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = _load_sigen_module("support_cache")
        self.store = MemoryStore()
        self.cache = self.module.SupportCache(None, "entry", store=self.store)
        await self.cache.async_load()
        self.identity = {"serial": "A", "firmware": "1"}

    async def test_partial_progress_survives_new_cache_instance(self):
        support = {"input": {"good": True, "absent": False}}
        self.cache.update("device", "schema", self.identity, support)
        support["input"]["good"] = False
        self.assertEqual(0, self.store.saves, "updates should be batched")
        await self.cache.async_flush()
        restored = self.module.SupportCache(None, "entry", store=self.store)
        await restored.async_load()
        states, status = restored.restore("device", "schema", self.identity)
        self.assertEqual("hit", status)
        self.assertEqual({"input": {"good": True, "absent": False}}, states)
        self.assertNotIn("values", self.store.data["devices"]["device"])
        self.assertIsNotNone(restored.age("device"))

    async def test_identity_or_definition_change_rejects_cache(self):
        self.cache.update("device", "schema", self.identity, {"input": {"good": True}})
        self.assertEqual(({}, "identity_changed"), self.cache.restore(
            "device", "schema", {"serial": "B", "firmware": "1"}
        ))
        self.assertEqual(({}, "definitions_changed"), self.cache.restore(
            "device", "new-schema", self.identity
        ))
        self.assertEqual(({}, "miss"), self.cache.restore("other-device", "schema", self.identity))

    async def test_unverified_identity_cannot_suppress_new_registers(self):
        self.cache.update("device", "schema", self.identity, {"input": {"good": True, "absent": False}})
        states, status = self.cache.restore("device", "schema", {})
        self.assertEqual("positive_only", status)
        self.assertEqual({"input": {"good": True}}, states)

    async def test_corrupt_or_unreadable_cache_does_not_break_discovery(self):
        class BrokenStore(MemoryStore):
            async def async_load(self):
                raise ValueError("corrupt")
        broken = self.module.SupportCache(None, "entry", store=BrokenStore())
        await broken.async_load()
        self.assertEqual(({}, "miss"), broken.restore("device", "schema", self.identity))
        self.assertEqual("ValueError", broken.last_error)

    async def test_unchanged_capabilities_do_not_keep_writing(self):
        self.cache.update("device", "schema", self.identity, {"input": {"good": True}})
        await self.cache.async_flush()
        self.cache.update("device", "schema", self.identity, {"input": {"good": True}})
        await self.cache.async_flush()
        self.assertEqual(1, self.store.saves)

    async def test_malformed_records_are_discarded_and_can_be_replaced(self):
        self.store.data = {"devices": {"device": ["bad"], "other": None}}
        await self.cache.async_load()
        self.assertEqual(({}, "miss"), self.cache.restore("device", "schema", self.identity))
        self.assertIsNone(self.cache.age("device"))
        self.cache.update("device", "schema", self.identity, {"input": {"good": True}})
        await self.cache.async_flush()
        self.assertEqual("hit", self.cache.restore("device", "schema", self.identity)[1])

    async def test_only_boolean_support_states_are_restored(self):
        self.store.data = {"devices": {"device": {
            "fingerprint": "schema", "identity": self.identity,
            "support": {"input": {"good": True, "absent": False, "bad": 1,
                                  "unknown": None}, "bad": []}}}}
        await self.cache.async_load()
        self.assertEqual(({"input": {"good": True, "absent": False}}, "hit"),
                         self.cache.restore("device", "schema", self.identity))

    async def test_failed_save_keeps_progress_for_a_later_retry(self):
        original = self.store.async_save
        async def failed_save(data):
            raise OSError("disk unavailable")
        self.store.async_save = failed_save
        self.cache.update("device", "schema", self.identity, {"input": {"good": True}})
        await self.cache.async_flush()
        self.assertEqual("OSError", self.cache.last_error)
        self.store.async_save = original
        await self.cache.async_flush()
        self.assertIsNone(self.cache.last_error)
        self.assertEqual(1, self.store.saves)

    async def test_binding_separates_units_roles_and_endpoints(self):
        key = self.module.device_cache_key
        binding = {"host": "one", "port": 502, "slave_id": 1}
        keys = {key("plant", binding), key("inverter", binding),
                key("plant", {**binding, "slave_id": 2}),
                key("plant", {**binding, "host": "two"})}
        self.assertEqual(4, len(keys))
