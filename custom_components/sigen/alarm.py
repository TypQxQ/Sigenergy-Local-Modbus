"""Alarm bitmaps whose meaning cannot be determined."""
from __future__ import annotations

from typing import Any


ALARM_REGISTER_GROUPS = {
    "plant_general_alarm1": "PCS_ALARM_CODES",
    "plant_general_alarm2": "PCS_ALARM_CODES2",
    "plant_general_alarm3": "ESS_ALARM_CODES",
    "plant_general_alarm4": "GATEWAY_ALARM_CODES",
    "plant_general_alarm5": "DC_CHARGER_ALARM_CODES",
    "plant_general_alarm6": "PLANT_ALARM_CODES6",
    "plant_general_alarm7": "PLANT_ALARM_CODES7",
    "inverter_alarm1": "PCS_ALARM_CODES",
    "inverter_alarm2": "PCS_ALARM_CODES2",
    "inverter_alarm3": "ESS_ALARM_CODES",
    "inverter_alarm4": "GATEWAY_ALARM_CODES",
    "inverter_alarm5": "DC_CHARGER_ALARM_CODES",
    "inverter_ess_alarm": "ESS_ALARM_CODES",
    "inverter_gateway_alarm": "GATEWAY_ALARM_CODES",
    "inverter_dc_charger_alarm": "DC_CHARGER_ALARM_CODES",
    "ac_charger_alarm1": "AC_CHARGER_ALARM_CODES1",
    "ac_charger_alarm2": "AC_CHARGER_ALARM_CODES2",
    "ac_charger_alarm3": "AC_CHARGER_ALARM_CODES3",
}


def alarm_state_attributes(
    register_name: str, value: Any, alarm_mapping: dict[int, str]
) -> dict[str, Any] | None:
    """Expose translatable bit keys without changing the legacy alarm state.

    HA translates each item in an attribute list using the frontend language.
    Keep unknown/reserved bits visible too; Alarm 7's ambiguous 0xFFFF must
    remain unknown rather than suggesting that its individual alarms fired.
    """
    if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        return None
    if unknown_alarm_value(register_name, value):
        return {
            "alarm_status": "unknown",
            "alarm_reason": "all_bits_set",
            "raw_value": value,
            "alarm_codes": ["unknown"],
        }
    codes = [f"bit_{bit}" for bit in range(16) if value & (1 << bit)]
    known_active = any(value & (1 << bit) for bit in alarm_mapping)
    return {
        "alarm_status": "no_problem" if value == 0 else "active" if known_active else "unknown",
        "alarm_codes": codes,
        "raw_value": value,
    }


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
