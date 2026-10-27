"""Alarm bitmaps whose meaning cannot be determined."""
from __future__ import annotations

from typing import Any


def unknown_alarm_value(register_name: str, value: Any) -> bool:
    """Recognize the ambiguous Alarm 7 bitmap, without masking other alarms.

    This is a presentation rule for register 30281, not a Modbus capability
    result. Keep reading it so a later known bitmap can be decoded normally.
    Other alarm registers can legitimately use all sixteen bits.
    """
    return register_name == "plant_general_alarm7" and value == 0xFFFF


def unknown_alarm_diagnostics(data: dict[str, Any] | None) -> dict[str, Any]:
    """Explain the current unknown alarm value without changing raw data."""
    key = "plant_general_alarm7"
    value = ((data or {}).get("plant") or {}).get(key)
    if not unknown_alarm_value(key, value):
        return {}
    return {
        key: {
            "status": "unknown",
            "reason": "all_bits_set",
            "address": 30281,
            "raw_value": 0xFFFF,
            "continues_polling": True,
        }
    }
