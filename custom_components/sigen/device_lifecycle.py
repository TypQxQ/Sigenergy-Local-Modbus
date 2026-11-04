"""Select an explicit registry subtree without relying on display names."""
from collections.abc import Iterable
from typing import Any


def removal_device_ids(devices: Iterable[Any], identifier: tuple[str, str]) -> list[str]:
    """Return descendants before their selected root; unknown roots remove nothing."""
    devices = list(devices)
    selected = [device.id for device in devices if identifier in device.identifiers]
    seen = set(selected)
    while True:
        children = [device.id for device in devices
                    if device.id not in seen and device.via_device_id in seen]
        if not children:
            return list(reversed(selected))
        selected.extend(children)
        seen.update(children)
