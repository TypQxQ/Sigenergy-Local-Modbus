"""modbus_connection Component classes built from the Sigenergy register tables.

`modbusregisterdefinitions.py` is the validated source of truth for every
Sigenergy register's address, word count, gain and read/write type -- it has
no transport dependency of its own. Rather than hand-transcribing those ~360
registers into `gauge()` / `uint32()` / ... calls (and risking a transposed
address or gain on a system that writes battery charge/discharge commands to
real hardware), `_make_component` builds each `Component` subclass directly
from the existing dictionaries. The address/gain data never passes through a
second, hand-typed copy.
"""
from __future__ import annotations

from typing import Literal

from modbus_connection.model import Component, RegisterField, gauge, int32, string, uint32, uint64

from .modbusregisterdefinitions import (
    AC_CHARGER_PARAMETER_REGISTERS,
    AC_CHARGER_RUNNING_INFO_REGISTERS,
    DC_CHARGER_PARAMETER_REGISTERS,
    DC_CHARGER_RUNNING_INFO_REGISTERS,
    INVERTER_PARAMETER_REGISTERS,
    INVERTER_RUNNING_INFO_REGISTERS,
    PLANT_ESS_PREHEATING_REGISTERS,
    PLANT_PARAMETER_REGISTERS,
    PLANT_RUNNING_INFO_REGISTERS,
    DataType,
    ModbusRegisterDefinition,
    RegisterType,
)

RegisterSpace = Literal["holding", "input"]


def _field_from_definition(reg: ModbusRegisterDefinition) -> RegisterField:
    """Build the modbus_connection field matching one Sigenergy register.

    Sigenergy's `gain` divides a decoded raw value (`value = raw / gain`) and
    multiplies a written one (`raw = value * gain`), which is exactly
    `scale = 1.0 / gain` in modbus_connection's field model (`value = raw *
    scale`). A `gain` of 1 maps to `scale = 1.0`, which modbus_connection
    already special-cases to pass integers through unscaled.

    Raises `ValueError` for a data type outside the set Sigenergy's protocol
    documentation actually uses.
    """
    writable = reg.register_type is not RegisterType.READ_ONLY
    scale = 1.0 / reg.gain if reg.gain else 1.0

    if reg.data_type is DataType.STRING:
        return string(reg.address, reg.count, writable=writable)
    if reg.data_type is DataType.U16:
        return gauge(reg.address, scale, signed=False, writable=writable)
    if reg.data_type is DataType.S16:
        return gauge(reg.address, scale, signed=True, writable=writable)
    if reg.data_type is DataType.U32:
        return uint32(reg.address, scale=scale, writable=writable)
    if reg.data_type is DataType.S32:
        return int32(reg.address, scale=scale, writable=writable)
    if reg.data_type is DataType.U64:
        return uint64(reg.address, scale=scale, writable=writable)
    raise ValueError(f"Unsupported Sigenergy data type: {reg.data_type}")


def _make_component(
    name: str,
    registers: dict[str, ModbusRegisterDefinition],
    *,
    register_space: RegisterSpace,
) -> type[Component]:
    """Dynamically build a `Component` subclass from a register dictionary.

    Equivalent to writing a `class {name}(Component):` body with one field
    per register, except every field comes straight from `registers` instead
    of being retyped by hand.
    """
    attrs: dict[str, object] = {"register_space": register_space}
    attrs.update(
        (register_name, _field_from_definition(reg))
        for register_name, reg in registers.items()
    )
    return type(name, (Component,), attrs)


def _split_write_only(
    registers: dict[str, ModbusRegisterDefinition],
) -> tuple[dict[str, ModbusRegisterDefinition], dict[str, ModbusRegisterDefinition]]:
    """Split a register dictionary into (readable, write-only).

    A write-only register must never enter a Component's read-block plan (the
    device may refuse to serve it, which would otherwise fail the whole
    block), so it is built into a separate Component that is only ever
    written through, never updated.
    """
    readable, write_only = {}, {}
    for register_name, reg in registers.items():
        (write_only if reg.register_type is RegisterType.WRITE_ONLY else readable)[
            register_name
        ] = reg
    return readable, write_only


# -- Plant --------------------------------------------------------------------

PlantRunningInfo = _make_component(
    "PlantRunningInfo", PLANT_RUNNING_INFO_REGISTERS, register_space="input"
)

_plant_params_ro, _plant_params_wo = _split_write_only(PLANT_PARAMETER_REGISTERS)
PlantParameters = _make_component("PlantParameters", _plant_params_ro, register_space="holding")
PlantCommands = _make_component("PlantCommands", _plant_params_wo, register_space="holding")

# PLANT_ESS_PREHEATING_REGISTERS carries no write-only entries today, so a
# single readable Component covers it; _split_write_only would otherwise be
# needed here too if the protocol doc ever adds a write-only preheating field.
PlantPreheating = _make_component(
    "PlantPreheating", PLANT_ESS_PREHEATING_REGISTERS, register_space="holding"
)

# -- Inverter -----------------------------------------------------------------

InverterRunningInfo = _make_component(
    "InverterRunningInfo", INVERTER_RUNNING_INFO_REGISTERS, register_space="input"
)

_inverter_params_ro, _inverter_params_wo = _split_write_only(INVERTER_PARAMETER_REGISTERS)
InverterParameters = _make_component(
    "InverterParameters", _inverter_params_ro, register_space="holding"
)
InverterCommands = _make_component(
    "InverterCommands", _inverter_params_wo, register_space="holding"
)

# -- AC Charger -----------------------------------------------------------------

ACChargerRunningInfo = _make_component(
    "ACChargerRunningInfo", AC_CHARGER_RUNNING_INFO_REGISTERS, register_space="input"
)

_ac_charger_params_ro, _ac_charger_params_wo = _split_write_only(AC_CHARGER_PARAMETER_REGISTERS)
ACChargerParameters = _make_component(
    "ACChargerParameters", _ac_charger_params_ro, register_space="holding"
)
ACChargerCommands = _make_component(
    "ACChargerCommands", _ac_charger_params_wo, register_space="holding"
)

# -- DC Charger -----------------------------------------------------------------
# A DC charger is not a separate Modbus unit: its registers are read from the
# same unit (and so the same ModbusUnit) as its parent inverter.

DCChargerRunningInfo = _make_component(
    "DCChargerRunningInfo", DC_CHARGER_RUNNING_INFO_REGISTERS, register_space="input"
)

_dc_charger_params_ro, _dc_charger_params_wo = _split_write_only(DC_CHARGER_PARAMETER_REGISTERS)
DCChargerParameters = _make_component(
    "DCChargerParameters", _dc_charger_params_ro, register_space="holding"
)
DCChargerCommands = _make_component(
    "DCChargerCommands", _dc_charger_params_wo, register_space="holding"
)
