"""Select coherent, unit-aware restore values for local PV energy integrators."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException, InvalidOperation
import logging
from time import monotonic
from typing import Any


_LOGGER = logging.getLogger(__name__)

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
    queue_wait: float | None = None
    query_time: float | None = None


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
        _LOGGER.debug("PV restore snapshot rejected: missing or invalid numeric state")
        return None
    updated = state.last_updated
    if updated is None or updated.utcoffset() is None:
        _LOGGER.debug("PV restore snapshot rejected: missing timezone-aware timestamp")
        return None
    value = (energy_in_kwh(native_data.native_value, native_data.native_unit_of_measurement)
             if native_data is not None else None)
    if value is None:
        _LOGGER.debug("PV restore snapshot native value/unit unavailable; trying stored state")
        candidate = sample_from_state(state)
        value = candidate.value if candidate else None
    if value is None:
        _LOGGER.debug("PV restore snapshot rejected: invalid energy value or unit")
    return EnergySample(value, updated, "snapshot") if value is not None else None


def read_recorder_restore(hass, entity_id: str, cutoff: datetime,
                          snapshot: EnergySample | None,
                          queued_at: float | None = None) -> RecorderRestore:
    """Measure executor queue time separately from the history query itself."""
    started = monotonic()
    queue_wait = max(0.0, started - queued_at) if queued_at is not None else None
    try:
        result = _read_recorder_restore(hass, entity_id, cutoff, snapshot)
    except Exception:
        _LOGGER.debug(
            "PV restore Recorder query failed for %s: queue_wait=%s s, query_time=%.3f s",
            entity_id, queue_wait, monotonic() - started, exc_info=True,
        )
        raise
    query_time = monotonic() - started
    _LOGGER.debug(
        "PV restore Recorder query completed for %s: queue_wait=%s s, "
        "query_time=%.3f s, has_history=%s, sample=%s",
        entity_id, queue_wait, query_time, result.has_history, result.sample,
    )
    return RecorderRestore(result.sample, result.has_history, queue_wait, query_time)


def _read_recorder_restore(hass, entity_id: str, cutoff: datetime,
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
        _LOGGER.debug("PV restore Recorder has no history for %s", entity_id)
        return RecorderRestore(None, False)

    _LOGGER.debug(
        "PV restore Recorder latest state unusable for %s "
        "(after cutoff or invalid value/unit); searching bounded pre-start history",
        entity_id,
    )
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
                   daily: bool, *, entity_id: str | None = None) -> EnergySample:
    """Use the newest valid sample, with native data winning an exact tie."""
    label = entity_id or "<unknown>"
    _LOGGER.debug(
        "PV restore candidates for %s: snapshot=%s, recorder=%s, cutoff=%s, "
        "now=%s, daily=%s, had_snapshot=%s, recorder_has_history=%s; values in kWh",
        label, snapshot, recorder.sample, cutoff.isoformat(), now.isoformat(),
        daily, had_snapshot, recorder.has_history,
    )
    candidates = []
    for source, sample in (("recorder", recorder.sample), ("snapshot", snapshot)):
        if sample is None:
            _LOGGER.debug("PV restore rejected %s candidate for %s: missing or invalid sample",
                          source, label)
        elif sample.updated > cutoff:
            _LOGGER.debug("PV restore rejected %s candidate for %s: timestamp after startup cutoff",
                          source, label)
        else:
            candidates.append(sample)
    if not candidates:
        if had_snapshot or recorder.has_history:
            _LOGGER.debug("PV restore decision for %s: no valid existing data; refusing zero",
                          label)
            raise ValueError("Existing PV energy has no valid restore sample")
        _LOGGER.debug("PV restore decision for %s: new_sensor, 0 kWh; no prior counter data",
                      label)
        return EnergySample(Decimal(0), now, "new_sensor")
    sample = max(candidates, key=lambda item: (item.updated, item.source == "snapshot"))
    if daily and sample.updated.astimezone(now.tzinfo).date() < now.date():
        _LOGGER.debug(
            "PV restore decision for %s: new_day, 0 kWh; newest sample %s "
            "belongs to a previous local day (%s)", label, sample, now.tzinfo,
        )
        return EnergySample(Decimal(0), now, "new_day")
    reason = "only valid candidate"
    if len(candidates) > 1:
        reason = ("native snapshot precision at equal timestamp"
                  if candidates[0].updated == candidates[1].updated else "newest timestamp")
    _LOGGER.debug("PV restore decision for %s: %s, %s kWh, timestamp=%s; %s",
                  label, sample.source, sample.value, sample.updated.isoformat(), reason)
    return sample
