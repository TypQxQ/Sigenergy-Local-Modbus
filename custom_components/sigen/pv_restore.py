"""Select coherent, unit-aware restore values for local PV energy integrators."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any


ENERGY_TO_KWH = {
    "mWh": Decimal("0.000001"), "Wh": Decimal("0.001"),
    "kWh": Decimal("1"), "MWh": Decimal("1000"),
    "GWh": Decimal("1000000"), "TWh": Decimal("1000000000"),
}


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def energy_in_kwh(value: Any, unit: str | None) -> Decimal | None:
    """Interpret a persisted value in its own unit, never today's display unit."""
    number = _number(value)
    factor = ENERGY_TO_KWH.get(unit) if isinstance(unit, str) else None
    if number is None or factor is None:
        return None
    try:
        return number * factor
    except DecimalException:
        return None


@dataclass(frozen=True)
class EnergySample:
    value: Decimal
    updated: datetime
    source: str


@dataclass(frozen=True)
class RecorderRestore:
    sample: EnergySample | None
    has_history: bool


def sample_from_state(state: Any) -> EnergySample | None:
    """Reject nonnumeric states and unknown units without substituting zero."""
    if state is None:
        return None
    value = energy_in_kwh(state.state, state.attributes.get("unit_of_measurement"))
    updated = state.last_updated
    if value is None or updated is None or updated.utcoffset() is None:
        return None
    return EnergySample(value, updated, "recorder")


def snapshot_sample(native_data: Any, state: Any) -> EnergySample | None:
    """Pair HA's native extra data with the numeric state's snapshot timestamp."""
    # An unavailable state's timestamp does not date its last valid native
    # value. Do not let it outrank newer numeric Recorder data.
    if state is None or _number(state.state) is None:
        return None
    updated = state.last_updated
    if updated is None or updated.utcoffset() is None:
        return None
    value = (energy_in_kwh(native_data.native_value, native_data.native_unit_of_measurement)
             if native_data is not None else None)
    if value is None:
        candidate = sample_from_state(state)
        value = candidate.value if candidate else None
    return EnergySample(value, updated, "snapshot") if value is not None else None


def read_recorder_restore(hass, entity_id: str, cutoff: datetime,
                          snapshot: EnergySample | None) -> RecorderRestore:
    """Run in Recorder's executor; normally one indexed last-state query.

    Only a trailing unavailable/invalid record requires a bounded history read.
    No post-start state is allowed to become this attempt's restore baseline.
    """
    from homeassistant.components.recorder.history import (
        get_last_state_changes, state_changes_during_period,
    )

    latest = get_last_state_changes(hass, 1, entity_id).get(entity_id, [])
    prior = [state for state in latest if state.last_updated < cutoff]
    for state in prior:
        if (candidate := sample_from_state(state)) is not None:
            return RecorderRestore(candidate, True)
    if not latest:
        return RecorderRestore(None, False)

    # Snapshots are normally minutes old. Without one, cover two recent days
    # for daily counters without scanning a lifetime of high-frequency states.
    start = snapshot.updated - timedelta(microseconds=1) if snapshot else cutoff - timedelta(days=2)
    states = state_changes_during_period(
        hass, start, cutoff, entity_id, no_attributes=False,
        include_start_time_state=False,
    ).get(entity_id, [])
    candidates = [candidate for state in states
                  if state.last_updated < cutoff
                  and (candidate := sample_from_state(state)) is not None]
    return RecorderRestore(max(candidates, key=lambda item: item.updated, default=None),
                           bool(prior or states))


def choose_restore(snapshot: EnergySample | None, recorder: RecorderRestore,
                   had_snapshot: bool, cutoff: datetime, now: datetime,
                   daily: bool) -> EnergySample:
    """Use the newest valid sample, with native data winning an exact tie."""
    candidates = [sample for sample in (recorder.sample, snapshot)
                  if sample is not None and sample.updated <= cutoff]
    if not candidates:
        if had_snapshot or recorder.has_history:
            raise ValueError("Existing PV energy has no valid restore sample")
        return EnergySample(Decimal(0), now, "new_sensor")
    sample = max(candidates, key=lambda item: (item.updated, item.source == "snapshot"))
    if daily and sample.updated.astimezone(now.tzinfo).date() < now.date():
        return EnergySample(Decimal(0), now, "new_day")
    return sample
