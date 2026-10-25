"""Firmware-specific alarm values that cannot describe a usable alarm bitmap."""
from __future__ import annotations

from typing import Any


def unsupported_alarm_value(register_name: str, value: Any) -> bool:
    """Recognize the reported Alarm 7 no-data value, without masking other alarms.

    This is a presentation rule for register 30281, not a Modbus capability
    result. Keep reading it so a later usable bitmap can recover automatically.
    Other alarm registers can legitimately use all sixteen bits.
    """
    return register_name == "plant_general_alarm7" and value == 0xFFFF


def unsupported_alarm_diagnostics(data: dict[str, Any] | None) -> dict[str, Any]:
    """Explain the current unsupported alarm value without changing raw data."""
    key = "plant_general_alarm7"
    value = ((data or {}).get("plant") or {}).get(key)
    if not unsupported_alarm_value(key, value):
        return {}
    return {
        key: {
            "status": "unsupported",
            "reason": "all_bits_set",
            "address": 30281,
            "raw_value": 0xFFFF,
            "continues_polling": True,
        }
    }
