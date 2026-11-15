# Sigenergy entities in Home Assistant

Use this reference to choose a sensor for a dashboard or a control for an
automation. The tables use the default English names from
Sigenergy ESS **2.0.3**.

| I want to… | Go to |
| --- | --- |
| Find or enable an entity and copy its ID | [Find an entity](#find-an-entity) |
| Check which models and accessories you need | [Which equipment do I need?](#which-equipment-do-i-need) |
| Understand the terms and units | [A few words used in the tables](#a-few-words-used-in-the-tables) |
| Use an entity in an automation | [Home Assistant actions](#home-assistant-actions) |
| Change EMS mode, cap a power flow or set inverter output | [Plant controls](#plant-controls) · [Remote EMS modes](#remote-ems-modes) · [Inverter controls](#inverter-controls) |
| Find a measurement, energy counter, status or alarm | [Plant sensors](#plant-sensors) · [Inverter sensors](#inverter-sensors) |
| Find my solar string or charger | [PV strings](#pv-strings) · [AC charger](#ac-charger) · [DC charger](#dc-charger) |

## A few words used in the tables

| Term | What it means |
| --- | --- |
| Power and energy | **Power** is how fast electricity is being used or made. **Energy** is how much was used or made over time. A 2 kW heater running for 30 minutes uses 1 kWh. |
| Grid, import and export | The **grid** is the public electricity network. **Import** means drawing power from it. **Export** means sending power back. |
| Plant, inverter and PCS | A **plant** is the whole installation. An **inverter** changes the electricity from solar panels or a battery into the kind your home uses. **PCS** stands for power conversion system and refers to the inverter side in these tables. |
| PV and string | **PV** means solar panels. A **string** is a connected group of panels. |
| ESS and SOC | **ESS** means the battery system. **SOC** is the battery's charge as a percentage. At 60% SOC, the battery is about 60% full. |
| EMS and setpoint | **EMS** is the system that decides where power should go. A **setpoint** is a value you ask the equipment to aim for. |
| Voltage, current and frequency | **Voltage** (V) is the electrical difference between two points that can make current flow. **Current** (A) is how much electric charge flows per second. **Frequency** (Hz) is how often the AC cycle repeats each second. |
| Phase | A three-phase connection uses three AC voltages that reach their peaks at different times. The system labels them A, B and C, or L1, L2 and L3. |
| Active, reactive and apparent power | **Active power** (kW) does useful work, such as heating a room. **Reactive power** (kvar) moves back and forth each AC cycle and can help support voltage. **Apparent power** (kVA) measures the total AC load the equipment must handle. |
| Power factor and Q/S | **Power factor** describes how much of the total AC power does useful work. **Q/S** compares reactive power with the total AC power. |

## Find an entity

1. Open **Settings → Devices & services → Entities** and filter by **Sigenergy
   ESS**. Use the device column to distinguish the plant, each inverter,
   each PV string and the AC or DC charger.
2. Include disabled entities if the table says **Disabled**. Open the
   entity's settings to enable it and copy its **Entity ID**. Use that ID in
   automations because device names, prior installations and renaming can
   change the ID you see.
3. To use controls, turn off **Read-only** in the Sigenergy integration's
   options. Even an enabled button needs write access.

**Default** applies to newly created entities. Older installations keep their
existing settings. Some entities appear only when the model, firmware and fitted
equipment support them. A temporarily **Unavailable** entity can be enabled
and still be unable to receive a command.

## Which equipment do I need?

Start with the inverter's **Model type** sensor under **Settings → Devices &
services → Sigenergy ESS**. Use the full model name, including the ending,
such as **SigenStor EC 12.0 SP** or **Sigen PV Max 10.0 TP**. If the sensor
has no reading, check the model shown on the device in mySigen or on its
label. **Firmware version** is the software running inside it. The model
tells you which family to look for. The firmware and equipment you have
installed affect which entities show up in Home Assistant.

### Match the label to your hardware

| Label in the table | What you need |
| --- | --- |
| **Hybrid** | An inverter in Sigenergy's hybrid group. This includes SigenStor EC (SP, TP or TPLV), Sigen Hybrid (SP, TP, SP2 or TP2), Sigen PV M1-HYA or M1-HYB, and PG Controller. Battery-related rows list **ESS** separately. |
| **PV** | An inverter in Sigenergy's solar inverter group. This includes Sigen PV Max (SP or TP) and Sigen PV M1, H1 or H2. |
| **Hybrid / PV** | Either inverter group. A slash between model names means **or**. |
| **ESS** | A battery system fitted to a supported SigenStor, Sigen Hybrid or Sigen PV M1-HYB installation. Look for this label on battery readings and controls. |
| **EVAC** | A supported AC car charger, such as a Sigen EVAC 4G T2, a Sigen EVAC 4G T2SH or a PG EVAC. Look for its own device in Home Assistant. |
| **EVDC** | A Sigen EV DC Charging Module fitted to a supported SigenStor or Sigen Hybrid and set up in the integration. |
| **Reported PV string** | A connected group of solar panels that the inverter reports as a separate string. Look under that string's device in Home Assistant. |
| **Integration** | A reading from Home Assistant itself, such as how long it took to fetch data. No extra hardware is needed. |

The middle dot (**·**) adds a second requirement. For example,
**Hybrid / PV · grid sensor** means either inverter group **and** a meter
or gateway that reports power flowing between your home and the public grid
to Sigenergy. Without that measurement, an inverter can still report how
much power it produces. It has no usable reading for overall grid import
or export. **Grid metering** needs the corresponding grid readings or
energy totals.

Other labels after the dot also describe something the installation must
report. A **configured smart load** is an appliance or circuit Sigenergy
tracks separately. **Third-party inverter telemetry** and **generator
telemetry** mean readings from an additional solar inverter or a generator.
**Load data** means the system can report how much power the home is using.
When a row says **supported output type**, check the inverter's **Output type**
sensor and the phases named in the row.

### When the exact model name matters

- **Sigen PV M1**, **Sigen PV M1-HYA** and **Sigen PV M1-HYB** are different
  models. The plain **M1** is in the PV group. The **M1-HYA** and **M1-HYB**
  are in the Hybrid group. The preheating controls list **M1-HYA or M1-HYB**.
  The grid import and export power-factor controls list **M1-HYB**.
- **Independent phase power control** goes further than the general Hybrid
  label. It requires a supported SigenStor, Sigen Hybrid or Sigen PV M1-HYB
  with **L1/L2/L3/N** output, which means three phases and a neutral wire.
- The **ride-through** and **frequency response** controls in this guide
  list **SigenStor or Sigen Hybrid**.

The model labels follow Sigenergy's V2.9 reference. Check **When it applies**
for any mode or wiring requirement and **Default** to see whether you need
to enable the entity in Home Assistant.

## Home Assistant actions

| Entity type | Read in Home Assistant | Action to change it |
| --- | --- | --- |
| Sensor or binary sensor | Measurement, status, alarm or an on/off indicator | — |
| Switch | On/off state | `switch.turn_on` or `switch.turn_off` |
| Dropdown | Current option | `select.select_option` with `option` |
| Number | Current setting | `number.set_value` with `value` in the entity's unit |
| Button | Time it was last pressed | `button.press` |

Home Assistant's **Actions** tool can build the YAML with your entity selected.
For dropdowns, use the option from that entity. For numbers, use the range shown
in Home Assistant and respect the equipment rating. For example, this action
selects **Maximum Self Consumption** once Remote EMS is enabled and the
selector is available. Replace the example Entity ID with your own.

```yaml
action: select.select_option
target:
  entity_id: select.example_remote_ems_control_mode
data:
  option: "Maximum Self Consumption"
```

**When it applies** tells you which mode or conditions the control needs.
**No specific EMS mode listed** means the product documentation gives no
mode requirement. After sending a command, check what actually happened
using a sensor such as **Battery power**.

## Plant controls

### EMS and plant commands

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| Remote EMS (controlled by Home Assistant) | `switch.turn_on` / `switch.turn_off` | Let Home Assistant choose how the system manages power. Turn this off to give up remote control. | Enabling it changes the plant EMS work mode to Remote EMS. | Hybrid / PV | Disabled |
| Remote EMS control mode | `select.select_option` | Choose a [Remote EMS mode](#remote-ems-modes), such as using your own solar power first or asking the battery to charge. | Remote EMS on. Home Assistant requires the enabled state to be read back. | Hybrid / PV | Disabled |
| Independent phase power control | `switch.turn_on` / `switch.turn_off` | Allow different power settings for each of the three AC phases. | L1/L2/L3/N output type. See the [phase setpoints](#per-phase-power-setpoints). | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Plant power | `switch.turn_on` / `switch.turn_off` | Start or stop the whole Sigenergy installation. Stopping it can cut its power output. | No specific EMS mode listed | Hybrid / PV | Disabled |
| Clear grid power loss lockout alarm | `button.press` | Clear the alarm that can keep the system locked after a grid outage. Check that the cause has been fixed first. | When the grid power loss lockout alarm is active. | Hybrid / PV | Enabled |

#### Remote EMS modes

Choose an option in **Remote EMS control mode**. Copy the exact option
text into YAML. Check **EMS work mode** to see whether the installation
has switched to Remote EMS.

| Dropdown option | What it does |
| --- | --- |
| PCS Remote Control | Lets Home Assistant tell the inverter how much power to deliver or take in. Remote EMS must be on. |
| Standby | Asks the installation to wait in standby mode. |
| Maximum Self Consumption | Tries to use more of your own solar energy at home. |
| Command Charging (Grid First) | Asks the battery to charge, using grid power first. |
| Command Charging (PV First) | Asks the battery to charge, using solar power first. |
| Command Discharging (PV First) | Asks the system to send power out, using solar power first. |
| Command Discharging (ESS First) | Asks the system to send power out, using battery power first. |
| V2G | Vehicle-to-grid lets a compatible car supply power through the charger. The car, charger and installation must support it. |

For automation, enable Remote EMS and wait for the enabled state and the mode
selector to become available. Changes to these two controls wait up to 60 seconds
for a fresh device readback. After a timeout, check the reported state before
retrying. Verify measured power separately when the goal is to change a power flow.
Use the explicit options above. In 2.0.1, selecting `Unknown` sends the
**PCS Remote Control** command.

### Power limits and battery charge levels

A power limit sets the most power that can flow. A **grid** limit applies
where your home connects to the public grid. A **PCS** limit applies where the
inverter connects to your home's AC wiring. If the inverter sends out 5 kW
and your home uses 3 kW, 2 kW goes to the grid when there are no other
power flows. If Home Assistant shows
`unknown`, it has no usable reading for that limit.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| ESS maximum charging power limit | `number.set_value` | Set how fast the battery is allowed to charge. Home Assistant offers 0 to 100 kW, up to the battery's rated charging power. | All EMS modes | ESS | Disabled |
| ESS maximum discharging power limit | `number.set_value` | Set how fast the battery is allowed to supply power. Home Assistant offers 0 to 100 kW, up to the battery's rated discharging power. | All EMS modes | ESS | Disabled |
| PV maximum power limit | `number.set_value` | Set the maximum power the system can take from the solar panels. Range in Home Assistant is 0 to 100 kW. | All EMS modes | Hybrid | Disabled |
| Grid export power limit | `number.set_value` | Set the most power your installation can send to the public grid. Range in Home Assistant is 0 to 100 kW. | All EMS modes | Hybrid / PV · grid sensor | Disabled |
| Grid import power limit | `number.set_value` | Set the most power your installation can draw from the public grid. Range in Home Assistant is 0 to 100 kW. | All EMS modes | Hybrid / PV · grid sensor | Disabled |
| PCS export power limit | `number.set_value` | Set the most power the inverter itself can send out through its AC connection. Range in Home Assistant is 0 to 100 kW. | All EMS modes | Hybrid / PV | Disabled |
| PCS import power limit | `number.set_value` | Set the most power the inverter itself can take in through its AC connection. Range in Home Assistant is 0 to 100 kW. | All EMS modes | Hybrid / PV | Disabled |
| ESS backup state of charge | `number.set_value` | Set how much battery charge to keep in reserve for a power cut. Use a percentage from 0 to 100%. | No specific EMS mode listed | ESS | Disabled |
| ESS charge cut-off state of charge | `number.set_value` | Set the battery percentage at which normal charging stops. Range is 0 to 100%. | No specific EMS mode listed | ESS | Disabled |
| ESS discharge cut-off state of charge | `number.set_value` | Set the battery percentage at which normal discharging stops. Range is 0 to 100%. | No specific EMS mode listed | ESS | Disabled |
| Grid import power factor adjustment | `number.set_value` | Set the power factor while drawing power from the grid. The allowed ranges are −0.999 to −0.8 and 0.8 to 1. | During grid import. With a grid sensor it targets the grid connection point. Without a grid sensor it targets inverter power. | Sigen PV M1-HYB | Disabled |
| Grid export power factor adjustment | `number.set_value` | Set the power factor while sending power to the grid. The allowed ranges are −0.999 to −0.8 and 0.8 to 1. | During grid export. With a grid sensor it targets the grid connection point. Without a grid sensor it targets inverter power. | Sigen PV M1-HYB | Disabled |

### Direct PCS power setpoints

These setpoints apply at the inverter. In Home Assistant, turn on
**Remote EMS (controlled by Home Assistant)** and select **PCS Remote Control**
in **Remote EMS control mode**. The number entities can still be available in
other modes. Check which mode is active before relying on their effect.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| Active power fixed adjustment | `number.set_value` | Ask the inverter to deliver or absorb a specific amount of useful power in kW. Check the available maximum and minimum active power sensors before setting it. | Remote EMS on and PCS Remote Control selected. | Hybrid / PV | Disabled |
| Reactive power fixed adjustment | `number.set_value` | Ask for a specific amount of reactive power in kvar. This is the part of AC power that moves back and forth. Check the available maximum and minimum reactive power sensors before setting it. | Remote EMS on and PCS Remote Control selected. | Hybrid / PV | Disabled |
| Active power percentage adjustment | `number.set_value` | Ask for useful power as a percentage of the equipment's power rating. Range in Home Assistant is −100 to 100%. | Remote EMS on and PCS Remote Control selected. | Hybrid / PV | Disabled |
| Q/S adjustment | `number.set_value` | Set how much of the total AC power is reactive power. Range in Home Assistant is −60 to 60%. | Remote EMS on and PCS Remote Control selected. | Hybrid / PV | Disabled |
| Power factor adjustment | `number.set_value` | Set the share of total AC power you want doing useful work. The allowed ranges are above −1 through −0.8, and 0.8 through 1. | Remote EMS on and PCS Remote Control selected. | Hybrid / PV | Disabled |

### Per-phase power setpoints

These also require the **L1/L2/L3/N** output type and the
**Independent phase power control** switch. Check the inverter's **Output type**
sensor. The phase controls become available when Home Assistant reads the
switch as on.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| Phase A active power fixed adjustment | `number.set_value` | Ask for a specific amount of useful power on phase A. Range is −100 to 100 kW. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase B active power fixed adjustment | `number.set_value` | Ask for a specific amount of useful power on phase B. Range is −100 to 100 kW. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase C active power fixed adjustment | `number.set_value` | Ask for a specific amount of useful power on phase C. Range is −100 to 100 kW. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase A reactive power fixed adjustment | `number.set_value` | Ask for a specific amount of reactive power on phase A. Range is −100 to 100 kvar. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase B reactive power fixed adjustment | `number.set_value` | Ask for a specific amount of reactive power on phase B. Range is −100 to 100 kvar. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase C reactive power fixed adjustment | `number.set_value` | Ask for a specific amount of reactive power on phase C. Range is −100 to 100 kvar. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase A active power percentage | `number.set_value` | Ask for useful power on phase A as a percentage of the rated power. Range is −100 to 100%. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase B active power percentage | `number.set_value` | Ask for useful power on phase B as a percentage of the rated power. Range is −100 to 100%. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase C active power percentage | `number.set_value` | Ask for useful power on phase C as a percentage of the rated power. Range is −100 to 100%. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase A Q/S ratio | `number.set_value` | Set the reactive share of total AC power on phase A. Range is −60 to 60%. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase B Q/S ratio | `number.set_value` | Set the reactive share of total AC power on phase B. Range is −60 to 60%. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |
| Phase C Q/S ratio | `number.set_value` | Set the reactive share of total AC power on phase C. Range is −60 to 60%. | Remote EMS on, PCS Remote Control selected, and phase control on. Requires L1/L2/L3/N. | SigenStor / Sigen Hybrid / M1-HYB | Disabled |

### Grid-code settings

> [!WARNING]
> These settings affect how the inverter protects itself and responds when
> the grid voltage or frequency changes. Use your installer's settings and
> follow the grid operator's requirements.

**Ride-through** means staying connected for a short time while grid voltage
is outside its usual range. **LVRT** is for low voltage. **HVRT** is for high
voltage. **Grid code rated frequency** shows the frequency the system is set
up for. The frequency settings below use it as their starting point.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| [Grid code] active power regulation gradient | `number.set_value` | Set how quickly useful power can change. Home Assistant offers 0 to 5% of rated power per second. | No specific EMS mode listed | Hybrid / PV | Disabled |
| [Grid code] LVRT enable | `select.select_option` | Allow the inverter to stay connected briefly when grid voltage goes too low. Choose `Disabled` or `Enabled`. | During low-voltage grid events after enabling. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] LVRT reactive power compensation factor | `number.set_value` | Set how strongly the inverter helps support voltage when it goes too low. Range is 0 to 10. | LVRT on and grid voltage too low. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] LVRT negative-sequence reactive power compensation factor | `number.set_value` | Set how strongly the inverter responds to uneven phase voltages during a low-voltage event. Range is 0 to 10. | LVRT on and grid voltage too low. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] LVRT mode | `select.select_option` | Choose how the inverter should behave while it stays connected during a low-voltage event. | LVRT on and grid voltage too low. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] LVRT grid voltage protection blocking | `select.select_option` | Choose whether to block the usual grid-voltage protection during a low-voltage event. Options are `Not Block` or `Block`. | LVRT on and grid voltage too low. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] HVRT enable | `select.select_option` | Allow the inverter to stay connected briefly when grid voltage goes too high. Choose `Disabled` or `Enabled`. | During high-voltage grid events after enabling. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] HVRT reactive power compensation factor | `number.set_value` | Set how strongly the inverter helps support voltage when it goes too high. Range is 0 to 10. | HVRT on and grid voltage too high. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] HVRT negative-sequence reactive power compensation factor | `number.set_value` | Set how strongly the inverter responds to uneven phase voltages during a high-voltage event. Range is 0 to 10. | HVRT on and grid voltage too high. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] HVRT mode | `select.select_option` | Choose how the inverter should behave while it stays connected during a high-voltage event. | HVRT on and grid voltage too high. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] HVRT grid voltage protection blocking | `select.select_option` | Choose whether to block the usual grid-voltage protection during a high-voltage event. Options are `Not Block` or `Block`. | HVRT on and grid voltage too high. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] over-frequency derating enable | `select.select_option` | Allow the inverter to reduce output when grid frequency is higher than normal. Choose `Disabled` or `Enabled`. | When frequency reaches the configured over-frequency range after enabling. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] over-frequency derating power ramp rate | `number.set_value` | Set how fast the inverter changes output during power reduction. Range is 0 to 100%. | Over-frequency derating enabled. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] over-frequency derating trigger frequency | `number.set_value` | Set the frequency where power reduction starts. Home Assistant shows 40 to 72 Hz. Use 100% to 120% of rated grid frequency. | Over-frequency derating enabled. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] over-frequency derating cut-off frequency | `number.set_value` | Set the frequency the inverter uses as the cut-off for power reduction. Home Assistant shows 40 to 72 Hz. Use 100% to 120% of rated grid frequency. | Over-frequency derating enabled. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] under-frequency power boost enable | `select.select_option` | Allow the inverter to increase output when grid frequency is lower than normal. Choose `Disabled` or `Enabled`. | When frequency reaches the configured under-frequency range after enabling. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] under-frequency power boost power ramp rate | `number.set_value` | Set how fast the inverter changes output during power boost. Range is 0 to 100%. | Under-frequency boost enabled. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] under-frequency power boost trigger frequency | `number.set_value` | Set the frequency where power boost starts. Home Assistant shows 40 to 72 Hz. Use 80% to 100% of rated grid frequency. | Under-frequency boost enabled. | SigenStor / Sigen Hybrid | Disabled |
| [Grid code] under-frequency power boost cut-off frequency | `number.set_value` | Set the frequency the inverter uses as the cut-off for power boost. Home Assistant shows 40 to 72 Hz. Use 80% to 100% of rated grid frequency. | Under-frequency boost enabled. | SigenStor / Sigen Hybrid | Disabled |

### Battery preheating

These controls are for **Sigen PV M1-HYA/HYB**. Select **Automatic** or
**Manual** in **ESS preheating mode**. Each scheduled time window (TOU) has
three number entities. Enter the start and end as Unix timestamps. A Unix
timestamp represents a date and time as the number of seconds since
1 January 1970 at midnight UTC. Check the device timezone before scheduling. Positive target power requests
charging and negative target power requests discharging.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| ESS preheating enable | `switch.turn_on` / `switch.turn_off` | Allow the system to warm the battery before use. | No specific EMS mode listed | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating mode | `select.select_option` | Choose `Automatic` for the system to manage warming or `Manual` to use manual settings. | When preheating is enabled. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating advance enable | `switch.turn_on` / `switch.turn_off` | Allow the system to start warming the battery ahead of time. | Manual preheating mode. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating reserved SOC | `number.set_value` | Set the battery percentage to keep in reserve for preheating. Range is 0 to 100%. | No specific EMS mode listed | Sigen PV M1-HYA/HYB | Disabled |

<details>
<summary>All 30 preheating time windows · 90 controls</summary>

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| ESS preheating TOU slot 1 start time | `number.set_value` | Set when scheduled window 1 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 1 end time | `number.set_value` | Set when scheduled window 1 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 1 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 1. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 2 start time | `number.set_value` | Set when scheduled window 2 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 2 end time | `number.set_value` | Set when scheduled window 2 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 2 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 2. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 3 start time | `number.set_value` | Set when scheduled window 3 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 3 end time | `number.set_value` | Set when scheduled window 3 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 3 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 3. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 4 start time | `number.set_value` | Set when scheduled window 4 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 4 end time | `number.set_value` | Set when scheduled window 4 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 4 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 4. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 5 start time | `number.set_value` | Set when scheduled window 5 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 5 end time | `number.set_value` | Set when scheduled window 5 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 5 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 5. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 6 start time | `number.set_value` | Set when scheduled window 6 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 6 end time | `number.set_value` | Set when scheduled window 6 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 6 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 6. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 7 start time | `number.set_value` | Set when scheduled window 7 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 7 end time | `number.set_value` | Set when scheduled window 7 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 7 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 7. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 8 start time | `number.set_value` | Set when scheduled window 8 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 8 end time | `number.set_value` | Set when scheduled window 8 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 8 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 8. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 9 start time | `number.set_value` | Set when scheduled window 9 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 9 end time | `number.set_value` | Set when scheduled window 9 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 9 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 9. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 10 start time | `number.set_value` | Set when scheduled window 10 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 10 end time | `number.set_value` | Set when scheduled window 10 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 10 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 10. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 11 start time | `number.set_value` | Set when scheduled window 11 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 11 end time | `number.set_value` | Set when scheduled window 11 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 11 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 11. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 12 start time | `number.set_value` | Set when scheduled window 12 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 12 end time | `number.set_value` | Set when scheduled window 12 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 12 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 12. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 13 start time | `number.set_value` | Set when scheduled window 13 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 13 end time | `number.set_value` | Set when scheduled window 13 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 13 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 13. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 14 start time | `number.set_value` | Set when scheduled window 14 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 14 end time | `number.set_value` | Set when scheduled window 14 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 14 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 14. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 15 start time | `number.set_value` | Set when scheduled window 15 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 15 end time | `number.set_value` | Set when scheduled window 15 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 15 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 15. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 16 start time | `number.set_value` | Set when scheduled window 16 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 16 end time | `number.set_value` | Set when scheduled window 16 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 16 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 16. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 17 start time | `number.set_value` | Set when scheduled window 17 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 17 end time | `number.set_value` | Set when scheduled window 17 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 17 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 17. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 18 start time | `number.set_value` | Set when scheduled window 18 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 18 end time | `number.set_value` | Set when scheduled window 18 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 18 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 18. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 19 start time | `number.set_value` | Set when scheduled window 19 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 19 end time | `number.set_value` | Set when scheduled window 19 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 19 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 19. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 20 start time | `number.set_value` | Set when scheduled window 20 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 20 end time | `number.set_value` | Set when scheduled window 20 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 20 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 20. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 21 start time | `number.set_value` | Set when scheduled window 21 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 21 end time | `number.set_value` | Set when scheduled window 21 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 21 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 21. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 22 start time | `number.set_value` | Set when scheduled window 22 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 22 end time | `number.set_value` | Set when scheduled window 22 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 22 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 22. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 23 start time | `number.set_value` | Set when scheduled window 23 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 23 end time | `number.set_value` | Set when scheduled window 23 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 23 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 23. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 24 start time | `number.set_value` | Set when scheduled window 24 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 24 end time | `number.set_value` | Set when scheduled window 24 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 24 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 24. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 25 start time | `number.set_value` | Set when scheduled window 25 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 25 end time | `number.set_value` | Set when scheduled window 25 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 25 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 25. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 26 start time | `number.set_value` | Set when scheduled window 26 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 26 end time | `number.set_value` | Set when scheduled window 26 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 26 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 26. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 27 start time | `number.set_value` | Set when scheduled window 27 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 27 end time | `number.set_value` | Set when scheduled window 27 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 27 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 27. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 28 start time | `number.set_value` | Set when scheduled window 28 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 28 end time | `number.set_value` | Set when scheduled window 28 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 28 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 28. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 29 start time | `number.set_value` | Set when scheduled window 29 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 29 end time | `number.set_value` | Set when scheduled window 29 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 29 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 29. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 30 start time | `number.set_value` | Set when scheduled window 30 starts. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 30 end time | `number.set_value` | Set when scheduled window 30 ends. Enter the date and time as a Unix timestamp in seconds. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |
| ESS preheating TOU slot 30 target power | `number.set_value` | Set how fast the battery should charge or discharge during window 30. Range is −100 to 100 kW. | During the scheduled window, with preheating set up. | Sigen PV M1-HYA/HYB | Disabled |

</details>

## Plant sensors

Expand a group to find its read-only entities. Grid power is positive for
import and negative for export. Battery power is positive during charging
and negative during discharging.

In automation conditions, **Grid connection status** uses `on_grid`,
`off_grid_auto` and `off_grid_manual`. Treat `unavailable` as loss of a
usable reading.

<details>
<summary>Power flows and electrical measurements · 28</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| Grid active power | How much power is coming from the public grid or being sent back to it right now. | kW | Hybrid / PV · grid sensor | Enabled |
| Grid reactive power | How much power is moving back and forth between the installation and the grid each cycle. | kvar | Hybrid / PV · grid sensor | Disabled |
| Maximum active power | The upper limit the system reports for useful power. | kW | Hybrid / PV | Enabled |
| Maximum apparent power | The equipment's limit for the total AC electrical load it can handle. | kVA | Hybrid / PV | Disabled |
| Phase A active power | How much useful power is flowing on phase A right now. | kW | Hybrid / PV | Disabled |
| Phase B active power | How much useful power is flowing on phase B right now. | kW | Hybrid / PV | Disabled |
| Phase C active power | How much useful power is flowing on phase C right now. | kW | Hybrid / PV | Disabled |
| Phase A reactive power | How much power is moving back and forth on phase A. | kvar | Hybrid / PV | Disabled |
| Phase B reactive power | How much power is moving back and forth on phase B. | kvar | Hybrid / PV | Disabled |
| Phase C reactive power | How much power is moving back and forth on phase C. | kvar | Hybrid / PV | Disabled |
| Grid code rated frequency | The grid frequency the system is set up for, such as 50 Hz. | Hz | Hybrid / PV | Disabled |
| Grid code rated voltage | The normal grid voltage the system is set up for. | V | Hybrid / PV | Disabled |
| Current control command value | The current output command as a percentage. This reading is for a remote output-control feature used in Japan. | % | Hybrid / PV · Japan remote output control | Disabled |
| Total load power | How much power all loads counted by the system are using right now. | kW | Hybrid / PV | Enabled |
| Active power | The power being delivered or taken in right now, measured in kW. | kW | Hybrid / PV | Enabled |
| Reactive power | The power that moves back and forth in the AC circuit and can help support voltage. | kvar | Hybrid / PV | Disabled |
| Sigen PV power | How much power the panels connected to Sigenergy inverters are making right now. | kW | Hybrid / PV | Disabled |
| Third-party PV power | How much power the other solar inverter is making right now. | kW | Hybrid / PV · third-party inverter telemetry | Disabled |
| Available maximum active power | The most useful power the system can send out right now. | kW | Hybrid / PV | Disabled |
| Available minimum active power | The lower end of the current power range. A negative value means the system can take power in. | kW | Hybrid | Disabled |
| Available maximum reactive power | The most reactive power the system can supply right now. | kvar | Hybrid / PV | Disabled |
| Available minimum reactive power | The lower end of the current reactive-power range. A negative value means the system can absorb it. | kvar | Hybrid / PV | Disabled |
| Grid phase A active power | How much useful power is flowing on phase A at the grid connection right now. | kW | Hybrid / PV · grid sensor | Disabled |
| Grid phase B active power | How much useful power is flowing on phase B at the grid connection right now. | kW | Hybrid / PV · grid sensor | Disabled |
| Grid phase C active power | How much useful power is flowing on phase C at the grid connection right now. | kW | Hybrid / PV · grid sensor | Disabled |
| Grid phase A reactive power | How much power is moving back and forth on phase A at the grid connection. | kvar | Hybrid / PV · grid sensor | Disabled |
| Grid phase B reactive power | How much power is moving back and forth on phase B at the grid connection. | kvar | Hybrid / PV · grid sensor | Disabled |
| Grid phase C reactive power | How much power is moving back and forth on phase C at the grid connection. | kvar | Hybrid / PV · grid sensor | Disabled |

</details>

<details>
<summary>Battery readings · 13</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| Battery state of charge | How full the battery is. | % | ESS | Enabled |
| Battery power | How fast the battery is charging or supplying power. Positive means charging. | kW | ESS | Enabled |
| Available maximum charging power | How fast the battery can charge right now. | kW | ESS | Disabled |
| Available maximum discharging power | How fast the battery can supply power right now. | kW | ESS | Disabled |
| ESS rated charging power | The battery system's rated maximum charging speed. | kW | ESS | Enabled |
| ESS rated discharging power | The battery system's rated maximum speed for supplying power. | kW | ESS | Enabled |
| Available maximum charging capacity | How much more energy the battery can take in under its current limits. | kWh | ESS | Disabled |
| Available maximum discharging capacity | How much energy the battery can still give out under its current limits. | kWh | ESS | Disabled |
| Rated energy capacity | How much energy the battery is designed to store. | kWh | ESS | Enabled |
| Charge cut-off SOC | The battery level at which normal charging stops. | % | ESS | Disabled |
| Discharge cut-off SOC | The battery level at which normal discharging stops. | % | ESS | Disabled |
| Battery state of health | The equipment's estimate of the battery's condition. This can fall as the battery wears. | % | ESS | Enabled |
| ESS average cell temperature | The average temperature of the small cells inside the battery. | °C | ESS | Enabled |

</details>

<details>
<summary>Energy counters · 15</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| PV daily generation | The solar energy the installation has produced so far today. | kWh | Hybrid / PV | Enabled |
| PV previous day generation | The solar energy the installation produced yesterday. | kWh | Hybrid / PV | Enabled |
| Total PV generation | The running total of solar energy reported for the installation. | kWh | Hybrid / PV | Enabled |
| Daily load consumption | The energy the monitored loads have used so far today. | kWh | Hybrid / PV | Enabled |
| Total load consumption | The running total of energy used by the monitored loads. | kWh | Hybrid / PV | Enabled |
| Total generation of third party inverter | The running total of solar energy made by the other inverter. | kWh | Hybrid / PV · third-party inverter telemetry | Disabled |
| Total charged energy of the ESS | The running total of energy sent into the battery. | kWh | Hybrid / PV · ESS fitted | Enabled |
| Total discharged energy of the ESS | The running total of energy the battery has supplied. | kWh | Hybrid / PV · ESS fitted | Enabled |
| Total charged energy of the EVDC | The running total of energy sent to cars through the DC charger. | kWh | Hybrid / PV · EVDC fitted | Disabled |
| Total discharged energy of the EVDC | The running total of energy taken from cars through the DC charger. | kWh | Hybrid / PV · EVDC fitted | Disabled |
| Total imported energy | The running total of energy bought or drawn from the grid. | kWh | Hybrid / PV · grid metering | Enabled |
| Total exported energy | The running total of energy sent back to the grid. | kWh | Hybrid / PV · grid metering | Enabled |
| Total energy output of oil fueled generator | The running total of energy supplied by a connected fuel-powered generator. | kWh | Hybrid / PV · generator telemetry | Disabled |
| Total charged energy of the EVAC | The running total of energy sent to cars through the AC charger. | kWh | Hybrid / PV · EVAC fitted | Disabled |
| Total generation of self PV | The running total of energy made by the installation's own solar equipment. | kWh | Hybrid / PV | Disabled |

</details>

<details>
<summary>Status, time and alarms · 13</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| System time | The time reported by the Sigenergy system. | Timestamp | Hybrid / PV | Disabled |
| System timezone | The time zone used by the Sigenergy system. | Time zone | Hybrid / PV | Disabled |
| EMS work mode | The plan the system is following to manage power, such as self-consumption or Remote EMS. | EMS mode | Hybrid / PV | Enabled |
| Grid sensor status | Whether the meter or gateway that measures the grid connection is online. | Status | Hybrid / PV | Disabled |
| Grid connection status | Whether the installation is connected to the grid or running on its own. | Status | Hybrid | Enabled |
| General alarm 1 | Inverter or grid problems, such as overheating, wiring trouble or a grid outage. | Alarm | Hybrid / PV | Enabled |
| General alarm 2 | Other inverter faults, such as communication, startup or electrical protection problems. | Alarm | Hybrid / PV | Enabled |
| General alarm 3 | Battery problems, such as temperatures outside the allowed range or an internal fault. | Alarm | Hybrid | Enabled |
| General alarm 4 | Gateway problems, such as a wiring fault or the loss of one phase. | Alarm | Hybrid / PV | Enabled |
| General alarm 5 | DC charger problems, such as overheating or a charging fault. | Alarm | Hybrid | Enabled |
| General alarm 6 | Problems with equipment across the installation, such as a lost meter connection or a generator that failed to start. | Alarm | Hybrid / PV | Enabled |
| General alarm 7 | Problems reported by grid protection equipment. An unknown state means Home Assistant has no reliable alarm result. | Alarm | Hybrid / PV | Enabled |
| Running state | The equipment's current operating state, such as running, waiting or in a fault state. | Status | Hybrid / PV | Enabled |

</details>

<details>
<summary>Derived values, indicators and diagnostics · 17</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| PV generating | Turns on when the solar panels are making enough power to pass the indicator's threshold. | `on` / `off` | Hybrid / PV · PV power source | Enabled |
| Battery charging | Turns on when enough power is going into the battery. | `on` / `off` | ESS | Enabled |
| Battery discharging | Turns on when enough power is coming out of the battery. | `on` / `off` | ESS | Enabled |
| Exporting to grid | Turns on when enough power is being sent back to the public grid. | `on` / `off` | Hybrid / PV · grid sensor | Enabled |
| Importing from grid | Turns on when enough power is being drawn from the public grid. | `on` / `off` | Hybrid / PV · grid sensor | Enabled |
| Daily grid import energy | How much energy the installation has drawn from the grid so far today. | kWh | Hybrid / PV · grid metering | Enabled |
| Daily grid export energy | How much energy the installation has sent back to the grid so far today. | kWh | Hybrid / PV · grid metering | Enabled |
| Daily third party inverter energy | How much energy the other solar inverter has made so far today. | kWh | Hybrid / PV · third-party inverter telemetry | Enabled |
| PV power | How much power the whole solar installation is making right now, including any other solar inverter. | kW | Hybrid / PV · PV power source | Enabled |
| Grid import power | How fast electricity is coming in from the public grid right now. | kW | Hybrid / PV · grid sensor | Enabled |
| Grid export power | How fast electricity is being sent back to the public grid right now. | kW | Hybrid / PV · grid sensor | Enabled |
| Consumed power | How much power the home's regular loads are using right now. | kW | Hybrid / PV · load data | Enabled |
| Daily PV energy | How much solar energy has been produced so far today. | kWh | Hybrid / PV · at least one reporting inverter | Enabled |
| Daily battery charge energy | How much energy has gone into the battery so far today. | kWh | ESS · at least one reporting inverter | Enabled |
| Daily battery discharge energy | How much energy the battery has supplied so far today. | kWh | ESS · at least one reporting inverter | Enabled |
| Modbus maximum data fetch time since restart | The longest time Home Assistant has taken to fetch data from Sigenergy since it started. | s | Integration | Enabled |
| Modbus fetch time | How long Home Assistant took to fetch the latest Sigenergy data. | s | Integration | Disabled |

</details>

<details>
<summary>Smart loads 1–24 · 48</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| [Smart load 1] total consumption | The running total of energy used by smart load 1. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 2] total consumption | The running total of energy used by smart load 2. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 3] total consumption | The running total of energy used by smart load 3. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 4] total consumption | The running total of energy used by smart load 4. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 5] total consumption | The running total of energy used by smart load 5. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 6] total consumption | The running total of energy used by smart load 6. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 7] total consumption | The running total of energy used by smart load 7. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 8] total consumption | The running total of energy used by smart load 8. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 9] total consumption | The running total of energy used by smart load 9. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 10] total consumption | The running total of energy used by smart load 10. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 11] total consumption | The running total of energy used by smart load 11. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 12] total consumption | The running total of energy used by smart load 12. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 13] total consumption | The running total of energy used by smart load 13. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 14] total consumption | The running total of energy used by smart load 14. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 15] total consumption | The running total of energy used by smart load 15. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 16] total consumption | The running total of energy used by smart load 16. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 17] total consumption | The running total of energy used by smart load 17. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 18] total consumption | The running total of energy used by smart load 18. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 19] total consumption | The running total of energy used by smart load 19. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 20] total consumption | The running total of energy used by smart load 20. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 21] total consumption | The running total of energy used by smart load 21. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 22] total consumption | The running total of energy used by smart load 22. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 23] total consumption | The running total of energy used by smart load 23. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 24] total consumption | The running total of energy used by smart load 24. | kWh | Hybrid / PV · configured smart load | Disabled |
| [Smart load 1] power | How much power smart load 1 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 2] power | How much power smart load 2 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 3] power | How much power smart load 3 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 4] power | How much power smart load 4 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 5] power | How much power smart load 5 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 6] power | How much power smart load 6 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 7] power | How much power smart load 7 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 8] power | How much power smart load 8 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 9] power | How much power smart load 9 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 10] power | How much power smart load 10 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 11] power | How much power smart load 11 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 12] power | How much power smart load 12 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 13] power | How much power smart load 13 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 14] power | How much power smart load 14 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 15] power | How much power smart load 15 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 16] power | How much power smart load 16 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 17] power | How much power smart load 17 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 18] power | How much power smart load 18 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 19] power | How much power smart load 19 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 20] power | How much power smart load 20 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 21] power | How much power smart load 21 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 22] power | How much power smart load 22 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 23] power | How much power smart load 23 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |
| [Smart load 24] power | How much power smart load 24 is using right now. | kW | Hybrid / PV · configured smart load | Disabled |

</details>

## Inverter controls

Target the inverter device in Home Assistant when setting output on a
particular inverter. **Plant controls** apply to the whole installation.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| Active power fixed adjustment | `number.set_value` | Ask the inverter to deliver or absorb a specific amount of useful power in kW. Range is −100 to 100 kW. | No specific EMS mode listed | PV | Disabled |
| Reactive power fixed adjustment | `number.set_value` | Ask for a specific amount of reactive power in kvar. This is the part of AC power that moves back and forth. Range is −100 to 100 kvar. | No specific EMS mode listed | PV | Disabled |
| Active power percentage adjustment | `number.set_value` | Ask for useful power as a percentage of the equipment's power rating. Range in Home Assistant is −100 to 100%. | No specific EMS mode listed | PV | Disabled |
| Reactive power Q/S adjustment | `number.set_value` | Set how much of the inverter's total AC power is reactive power. Range in Home Assistant is −60 to 60%. | No specific EMS mode listed | PV | Disabled |
| Power factor adjustment | `number.set_value` | Set the share of total AC power you want doing useful work. Range in Home Assistant is −1 to 1. | No specific EMS mode listed | PV | Disabled |
| Inverter power | `switch.turn_on` / `switch.turn_off` | Start or stop this inverter. Stopping it can cut its power output. | No specific EMS mode listed | Hybrid / PV | Disabled |

## Inverter sensors

<details>
<summary>Power, voltage and current · 31</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| Rated active power | The useful power this inverter is designed to deliver in normal operation. | kW | Hybrid / PV | Disabled |
| Maximum apparent power | The equipment's limit for the total AC electrical load it can handle. | kVA | Hybrid / PV | Disabled |
| Maximum active power | The upper limit the system reports for useful power. | kW | Hybrid / PV | Disabled |
| Maximum absorption power | The most useful power the inverter can take in from its AC connection. | kW | Hybrid | Disabled |
| Maximum active power adjustment | The highest useful power the inverter reports it can be asked to deliver. | kW | Hybrid / PV | Disabled |
| Minimum active power adjustment | The lowest useful-power setting the inverter reports it can accept. | kW | Hybrid | Disabled |
| Maximum reactive power fed | The most reactive power the inverter can put into the AC circuit. | kvar | Hybrid / PV | Disabled |
| Maximum reactive power absorbed | The most reactive power the inverter can take from the AC circuit. | kvar | Hybrid / PV | Disabled |
| Active power | The power being delivered or taken in right now, measured in kW. | kW | Hybrid / PV | Enabled |
| Reactive power | The power that moves back and forth in the AC circuit and can help support voltage. | kvar | Hybrid / PV | Disabled |
| Grid frequency | How many times per second the grid's AC cycle repeats. | Hz | Hybrid / PV | Disabled |
| PCS internal temperature | How hot it is inside the inverter. | °C | Hybrid / PV | Enabled |
| Rated grid voltage | The normal grid voltage the inverter is designed for. | V | Hybrid / PV | Disabled |
| Rated grid frequency | The grid frequency the inverter is designed for. | Hz | Hybrid / PV | Disabled |
| A-B line voltage | The voltage between phases A and B. | V | Hybrid / PV · supported output type | Disabled |
| B-C line voltage | The voltage between phases B and C. | V | Hybrid / PV · supported output type | Disabled |
| C-A line voltage | The voltage between phases C and A. | V | Hybrid / PV · supported output type | Disabled |
| Phase A voltage | The voltage of phase A measured against the neutral wire. | V | Hybrid / PV | Disabled |
| Phase B voltage | The voltage of phase B measured against the neutral wire. | V | Hybrid / PV · supported output type | Disabled |
| Phase C voltage | The voltage of phase C measured against the neutral wire. | V | Hybrid / PV · supported output type | Disabled |
| Phase A current | How much current is flowing through phase A. | A | Hybrid / PV | Disabled |
| Phase B current | How much current is flowing through phase B. | A | Hybrid / PV · supported output type | Disabled |
| Phase C current | How much current is flowing through phase C. | A | Hybrid / PV · supported output type | Disabled |
| Power factor | The share of total AC power that does useful work. A magnitude near 1 means most of it does. | — | Hybrid / PV | Disabled |
| PV power | How much power the solar panels attached to this inverter are making right now. | kW | Hybrid / PV | Enabled |
| Insulation resistance | How well the wiring is electrically separated from ground. A low reading can point to an insulation problem. | MΩ | Hybrid / PV | Disabled |
| Active power fixed value adjustment feedback | The fixed useful-power setting the inverter reports back. | kW | Hybrid / PV | Disabled |
| Reactive power fixed value adjustment feedback | The fixed reactive-power setting the inverter reports back. | kvar | Hybrid / PV | Disabled |
| Active power percentage adjustment feedback | The useful-power percentage the inverter reports back. | % | Hybrid / PV | Disabled |
| Reactive power Q/S adjustment feedback | The reactive-power share of total AC power that the inverter reports back. | % | Hybrid / PV | Disabled |
| Power factor adjustment feedback | The power-factor setting the inverter reports back. | — | Hybrid / PV | Disabled |

</details>

<details>
<summary>Battery and energy readings · 24</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| Battery current (estimated) | An estimate of the electric current flowing into or out of the battery. | A | ESS | Disabled |
| Rated battery capacity | How much energy the battery attached to this inverter is designed to store. | kWh | ESS | Disabled |
| ESS rated charge power | How fast the battery attached to this inverter is designed to charge. | kW | ESS | Disabled |
| ESS rated discharge power | How fast the battery attached to this inverter is designed to give out power. | kW | ESS | Disabled |
| Daily charge energy | How much energy has gone into this battery so far today. | kWh | ESS | Enabled |
| Total charge energy | The running total of energy that has gone into this battery. | kWh | ESS | Enabled |
| Daily discharge energy | How much energy this battery has supplied so far today. | kWh | ESS | Enabled |
| Total discharge energy | The running total of energy supplied by this battery. | kWh | ESS | Enabled |
| Battery power | How fast the battery is charging or supplying power. Positive means charging. | kW | ESS | Enabled |
| Maximum battery charge power | How fast this battery can take in power right now. | kW | ESS | Disabled |
| Maximum battery discharge power | How fast this battery can supply power right now. | kW | ESS | Disabled |
| Available battery charge energy | How much more energy this battery can take in under its current limits. | kWh | ESS | Disabled |
| Available battery discharge energy | How much energy this battery can still give out under its current limits. | kWh | ESS | Disabled |
| Battery state of charge | How full the battery is. | % | ESS | Enabled |
| Battery state of health | The equipment's estimate of the battery's condition. This can fall as the battery wears. | % | ESS | Enabled |
| Battery average cell temperature | The average temperature across the cells in this battery. | °C | ESS | Enabled |
| Battery average cell voltage | The average electrical voltage of the cells in this battery. | V | ESS | Disabled |
| Battery maximum temperature | The hottest reading reported inside this battery. | °C | ESS | Disabled |
| Battery minimum temperature | The coldest reading reported inside this battery. | °C | ESS | Disabled |
| Battery maximum cell voltage | The highest voltage reading from an individual cell in this battery. | V | ESS | Disabled |
| Battery minimum cell voltage | The lowest voltage reading from an individual cell in this battery. | V | ESS | Disabled |
| Battery pack count | How many battery packs are connected to this inverter. | Count | ESS | Disabled |
| Daily PV energy | How much solar energy has been produced so far today. | kWh | Hybrid / PV | Enabled |
| Total PV energy | The running total of solar energy recorded by this inverter. | kWh | Hybrid / PV | Enabled |

</details>

<details>
<summary>Device details, status and alarms · 14</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| Startup time | The time the inverter reports that it started. | Timestamp | Hybrid / PV | Disabled |
| Shutdown time | The time the inverter reports that it stopped. | Timestamp | Hybrid / PV | Disabled |
| Model type | The inverter's model name. | Text | Hybrid / PV | Enabled |
| Serial number | The number that identifies this particular inverter. | Text | Hybrid / PV | Disabled |
| Firmware version | The version of the software running inside the inverter. | Text | Hybrid / PV | Enabled |
| Running state | The equipment's current operating state, such as running, waiting or in a fault state. | Status | Hybrid / PV | Enabled |
| Alarm 1 | Inverter or grid problems, such as overheating, wiring trouble or a grid outage. | Alarm | Hybrid / PV | Enabled |
| Alarm 2 | Other inverter faults, such as communication, startup or electrical protection problems. | Alarm | Hybrid / PV | Enabled |
| Alarm 3 | Battery problems, such as temperatures outside the allowed range or an internal fault. | Alarm | Hybrid | Enabled |
| Alarm 4 | Gateway problems, such as a wiring fault or the loss of one phase. | Alarm | Hybrid / PV | Enabled |
| Alarm 5 | DC charger problems, such as overheating or a charging fault. | Alarm | Hybrid | Enabled |
| Output type | How the inverter's AC side is wired, such as single-phase or three-phase. | Status | Hybrid / PV | Disabled |
| PV string count | How many groups of linked solar panels the inverter reports. | Count | Hybrid / PV | Disabled |
| MPPT count | How many parts of the inverter can independently adjust the panels to get the most power from them. | Count | Hybrid / PV | Disabled |

</details>

## PV strings

Look under the inverter's PV string devices, for example **Inverter PV1**.
Home Assistant creates five sensor types per reported string. The number of
strings depends on the inverter model. Sigenergy lists strings **1–16** for
Hybrid and PV families and **17–36** for PV models.

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| Voltage | The electrical voltage of this group of linked solar panels. | V | Reported PV string | Enabled |
| Current | How much electric current is flowing from this group of panels. | A | Reported PV string | Enabled |
| Power | How much power this group of solar panels is making right now. | kW | Reported PV string | Enabled |
| Accumulated energy | An estimate of all the energy this string has made while Home Assistant has been tracking it. | kWh | Reported PV string | Enabled |
| Daily energy | An estimate of the energy this string has made so far today. | kWh | Reported PV string | Enabled |

## AC charger

Use the entities under the AC charger device. **Charger output current**
sets the allowed current. **Rated current** and **Input breaker rated current**
are read-only equipment ratings.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| Start charging | `button.press` | Ask the charger to start a charging session. | Available when **System state** can be read and the charger is past Initializing and Not Connected. | EVAC | Enabled |
| Stop charging | `button.press` | Ask the charger to stop the charging session. | Available when **System state** can be read and the charger is past Initializing and Not Connected. | EVAC | Enabled |
| Charger output current | `number.set_value` | Set the maximum charging current. Use at least 6 A and at most the lower of **Rated current** and **Input breaker rated current**. | Both charger and breaker ratings must be available in Home Assistant. | EVAC | Disabled |

<details>
<summary>AC charger sensors · 10</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| System state | What the AC charger is doing, such as waiting for a car, charging or reporting a fault. | Status | EVAC | Enabled |
| Total energy consumed | The running total of energy used by this AC charger. | kWh | EVAC | Enabled |
| Charging power | How fast energy is going to the car right now. | kW | EVAC | Enabled |
| Rated power | How much power the charger is designed to deliver. | kW | EVAC | Disabled |
| Rated current | How much current the charger is designed to handle. | A | EVAC | Disabled |
| Rated voltage | The electrical voltage the charger is designed for. | V | EVAC | Disabled |
| Input breaker rated current | The current rating of the circuit breaker that supplies the charger. | A | EVAC | Disabled |
| Alarm 1 | AC charger supply or wiring problems, such as voltage outside the allowed range or too much current. | Alarm | EVAC | Enabled |
| Alarm 2 | AC charger hardware problems, such as a stuck switch or a faulty plug lock. | Alarm | EVAC | Enabled |
| Alarm 3 | AC charger overheating, cable faults or trouble talking to its meter. | Alarm | EVAC | Enabled |

</details>

## DC charger

Configure the inverter's DC charger in the integration to see its charger
device and entities. Use **Running state** and **Output power** to verify
how the charger responds to a command.

| Home Assistant entity | Action | What it changes | When it applies | Model / equipment | Default |
| --- | --- | --- | --- | --- | --- |
| Start charging | `button.press` | Ask the charger to start a charging session. | Requires a charger status other than Idle or charger-reported Unavailable. If status is missing, other charger readings can make the button available. | EVDC | Enabled |
| Stop charging | `button.press` | Ask the charger to stop the charging session. | Requires a charger status other than Idle or charger-reported Unavailable. If status is missing, other charger readings can make the button available. | EVDC | Enabled |
| Maximum charging power limit | `number.set_value` | Set the fastest rate at which this DC charger can send power to the car. Home Assistant offers 0 to 100 kW, up to the charger's rating. | No specific EMS mode listed | EVDC | Enabled |
| Maximum discharging power limit | `number.set_value` | Set the fastest rate at which this DC charger can take power from the car. Home Assistant offers 0 to 100 kW, up to the charger's rating. | No specific EMS mode listed | EVDC | Enabled |

<details>
<summary>DC charger sensors · 14</summary>

| Home Assistant entity | What it shows | Unit or state | Model / equipment | Default |
| --- | --- | --- | --- | --- |
| Vehicle battery voltage | The voltage the charger sees at the car's battery. | V | EVDC | Enabled |
| Charging current | How much electric current the DC charger is sending to the car right now. | A | EVDC | Enabled |
| Output power | How much power is flowing through the DC charger right now. | kW | EVDC | Enabled |
| Vehicle SOC | How full the car's battery is, as reported to the charger. | % | EVDC | Enabled |
| Charging energy this session | How much energy has gone into the car in this charging session. | kWh | EVDC | Enabled |
| Charging duration this session | How long the current charging session has lasted. | s | EVDC | Enabled |
| Running state | What the DC charger is doing, such as waiting, charging a car or taking power from it. | Status | EVDC | Enabled |
| Discharging current | How much electric current is coming out of the car right now. | A | EVDC | Enabled |
| Discharging energy this session | How much energy the car has supplied in this session. | kWh | EVDC | Enabled |
| Discharging duration this session | How long the current discharging session has lasted. | s | EVDC | Enabled |
| Total charging energy | The running total of energy sent to cars through this DC charger. | kWh | EVDC | Enabled |
| Total discharging energy | The running total of energy taken from cars through this DC charger. | kWh | EVDC | Enabled |
| Rated charging power | The rated maximum speed for sending power into a car. | kW | EVDC | Enabled |
| Rated discharging power | The rated maximum speed for taking power from a car. | kW | EVDC | Enabled |

</details>

## Sources

Operating conditions and model families follow [Sigenergy Modbus Protocol V2.9](../Modbus_reference_documentation/Sigenergy%20Modbus%20Protocol%20EN_V2.9.pdf).
See Home Assistant’s action guides for
[switches](https://www.home-assistant.io/integrations/switch/#using-the-actions),
[dropdowns](https://www.home-assistant.io/integrations/select/#list-of-actions),
[numbers](https://www.home-assistant.io/integrations/number/#list-of-actions)
and [buttons](https://www.home-assistant.io/integrations/button/#list-of-actions).
