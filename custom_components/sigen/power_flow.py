"""Optional hysteresis for binary indicators, independent of measured power."""
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping
from typing import Any

CONF_POWER_FLOW_HYSTERESIS = "power_flow_hysteresis"
CONF_POWER_FLOW_ON_W = "power_flow_on_w"
CONF_POWER_FLOW_OFF_W = "power_flow_off_w"
DEFAULT_POWER_FLOW_ON_W = 50
DEFAULT_POWER_FLOW_OFF_W = 10


def power_flow_thresholds(options: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    """Return validated on/off watts; malformed saved options keep legacy behavior."""
    if options.get(CONF_POWER_FLOW_HYSTERESIS) is not True:
        return None
    try:
        values = [options.get(CONF_POWER_FLOW_ON_W, DEFAULT_POWER_FLOW_ON_W),
                  options.get(CONF_POWER_FLOW_OFF_W, DEFAULT_POWER_FLOW_OFF_W)]
        if any(isinstance(value, bool) for value in values):
            return None
        on, off = (Decimal(str(value)) for value in values)
        if on.is_finite() and off.is_finite() and 0 <= off < on <= 1000000:
            return on, off
    except (InvalidOperation, TypeError, ValueError):
        pass
    return None


def hysteresis_state(
    power_kw: Any, previous: bool | None, thresholds: tuple[Decimal, Decimal],
) -> bool | None:
    """Switch on above the upper threshold and off at/below the lower one."""
    if power_kw is None or isinstance(power_kw, bool):
        return None
    try:
        watts = Decimal(str(power_kw)) * 1000
        if not watts.is_finite():
            return None
    except (InvalidOperation, TypeError, ValueError):
        return None
    on, off = thresholds
    if watts > on:
        return True
    if watts <= off:
        return False
    return previous is True
