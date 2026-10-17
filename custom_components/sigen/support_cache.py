"""Persist capabilities, never live measurements or write confirmations."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)


def device_cache_key(role: str, connection: dict[str, Any]) -> str:
    """Separate physical units and roles, independently of display names."""
    binding = [role, connection["host"], int(connection["port"]), int(connection["slave_id"])]
    return hashlib.sha256(json.dumps(binding).encode()).hexdigest()


class SupportCache:
    """A config-entry scoped, versioned, atomic capability store."""

    def __init__(self, hass, entry_id: str, *, store=None) -> None:
        if store is None:
            from homeassistant.helpers.storage import Store
            store = Store(hass, 1, f"sigen.support.{entry_id}", private=True, atomic_writes=True)
        self._store = store
        self._devices: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.last_error: str | None = None

    async def async_load(self) -> None:
        try:
            data = await self._store.async_load()
            if isinstance(data, dict) and isinstance(data.get("devices"), dict):
                self._devices = {key: record for key, record in data["devices"].items()
                                 if isinstance(record, dict)}
        except Exception as ex:
            # A cache must never prevent live discovery.
            self.last_error = type(ex).__name__
            _LOGGER.warning("Cannot load Sigenergy support cache (%s); rediscovering", self.last_error)

    def snapshot(self, key: str) -> dict:
        """Keep the loaded generation while provisional live results arrive."""
        record = self._devices.get(key)
        return deepcopy(record) if isinstance(record, dict) else {}

    def restore(self, key: str, fingerprint: str, identity: dict,
                *, record: dict | None = None) -> tuple[dict, str]:
        record = self._devices.get(key) if record is None else record
        if not isinstance(record, dict) or not record:
            return {}, "miss"
        if record.get("fingerprint") != fingerprint:
            return {}, "definitions_changed"
        previous = record.get("identity", {})
        if not isinstance(previous, dict):
            return {}, "invalid"
        if identity and previous and previous != identity:
            return {}, "identity_changed"
        support = record.get("support", {})
        if not isinstance(support, dict):
            return {}, "invalid"
        result = {
            component: {field: value for field, value in fields.items() if type(value) is bool}
            for component, fields in support.items() if isinstance(fields, dict)
        }
        if not identity or not previous:
            # Successful live reads safely validate cached positives. Without
            # verified hardware identity, negatives must be rediscovered.
            result = {name: {field: value for field, value in fields.items() if value}
                      for name, fields in result.items()}
            provisional = record.get("provisional", {})
            if isinstance(provisional, dict):
                for name, fields in provisional.items():
                    if isinstance(fields, dict):
                        result.setdefault(name, {}).update(
                            {field: True for field, value in fields.items() if value is True}
                        )
            return result, "positive_only"
        return deepcopy(result), "hit"

    def update(self, key: str, fingerprint: str, identity: dict, support: dict,
               discovery: dict | None = None, *, provisional: dict | None = None) -> None:
        record = {"fingerprint": fingerprint, "identity": deepcopy(identity),
                  "support": deepcopy(support)}
        if discovery is not None:
            record["discovery"] = deepcopy(discovery)
        if provisional is not None:
            record["provisional"] = deepcopy(provisional)
        previous = self._devices.get(key, {})
        if (all(previous.get(key) == value for key, value in record.items())
                and ("provisional" in previous) == (provisional is not None)):
            return
        record["saved_at"] = time.time()
        self._devices[key] = record
        self._dirty = True
        self._store.async_delay_save(self._snapshot, 1)

    def age(self, key: str) -> float | None:
        saved_at = self._devices.get(key, {}).get("saved_at")
        return max(0, time.time() - saved_at) if isinstance(saved_at, (int, float)) else None

    def _snapshot(self) -> dict:
        return {"devices": deepcopy(self._devices)}

    async def async_flush(self) -> None:
        if not self._dirty:
            return
        try:
            await self._store.async_save(self._snapshot())
            self._dirty = False
            self.last_error = None
        except Exception as ex:
            self.last_error = type(ex).__name__
            _LOGGER.warning("Cannot save Sigenergy support cache (%s)", self.last_error)
