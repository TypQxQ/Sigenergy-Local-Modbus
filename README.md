# Sigenergy ESS for Home Assistant

[![Release](https://img.shields.io/github/release/TypQxQ/Sigenergy-Local-Modbus.svg)](https://github.com/TypQxQ/Sigenergy-Local-Modbus/releases/latest)
[![License](https://img.shields.io/github/license/TypQxQ/Sigenergy-Local-Modbus.svg)](LICENSE)

Monitor and control your Sigenergy ESS locally over Modbus TCP: solar production,
battery and grid power, energy counters, EMS modes, and AC/DC chargers.
Includes English and Swedish names, status labels and setup dialogs.

**Upgrading to 2.0?** Eight status sensors use new stored values. Read the
[migration guide](docs/migrations/2.0.md) before updating automation comparisons.

## Requirements

- Home Assistant **2026.10+** (Python **3.14.2+**).
- Sigenergy firmware **SPC109+**, with **Modbus TCP enabled and saved** by your installer.
- Your inverter's IP address and device ID. Reserve its IP address in your router.
- AC chargers normally use ID **1**; assign inverters different IDs when sharing
  the connection. DC chargers are accessed through their inverter.

## Install

1. **HA 2026.11+:** open **Settings → Marketplace**, search for **Sigenergy ESS**
   and install it. Marketplace is built in; existing HACS installations migrate
   automatically. See [Home Assistant's Marketplace guide](https://contributors.home-assistant.io/marketplace/how-it-works/).
   **HA 2026.10:** [install HACS](https://www.hacs.xyz/docs/use/), then find and
   download **Sigenergy ESS** there.
2. Restart Home Assistant.
3. Continue with setup below. To try a beta, enable prereleases for this
   repository in Marketplace/HACS and select the beta version.

[![Open in Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=TypQxQ&repository=Sigenergy-Local-Modbus&category=integration)

[Open in HACS on HA 2026.10](https://my.home-assistant.io/redirect/hacs_repository/?owner=TypQxQ&repository=Sigenergy-Local-Modbus&category=integration)

**Manual installation:** download `sigen.zip` from a [release](https://github.com/TypQxQ/Sigenergy-Local-Modbus/releases),
extract its contents directly into `config/custom_components/sigen/`, then restart.
Use the installation ZIP, not GitHub's source archive.

## Set up

1. Open **Settings → Devices & services** and configure the discovered Sigenergy
   system, or choose **Add integration → Sigenergy ESS**.
2. Enter the inverter IP, port (**502** by default), and inverter device ID.
   The plant device ID defaults to **247**.
3. Add more devices through **Add integration → Sigenergy ESS**, selecting the
   existing plant. Devices in a plant should normally share the same IP and port.

Change read-only mode, polling interval and device settings using **Configure**
on the integration entry.

**Controls start in read-only mode.** To use them, disable read-only mode in the
integration options and enable the required control entities in Home Assistant.
Check each setting before changing it. For EMS automation sequencing and device
details, see the [usage guide](docs/USAGE.md).

## Troubleshooting

- Confirm your installer enabled Modbus TCP and pressed **Save**.
- Check IP address, device IDs, port and firewall rules.
- Download integration diagnostics and capture a debug log before reporting a
  problem: [log guide](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions/54).
- Ask in [Discussions](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions)
  or report a bug in [Issues](https://github.com/TypQxQ/Sigenergy-Local-Modbus/issues).

## Dashboards and tools

- [Energy Flow Dashboard](Dashboards/energy_flow_gauge/readme.md)
- [Energy price and charging chart](Dashboards/nordpool_energy_chart/readme.md)
- [Si-GCG's projects](https://github.com/Si-GCG/sigenergy_projects)
- [EMHASS guide](https://sigenergy.annable.me/)

Contributions welcome: add relevant tests under `tests/` and open a pull request.
Licensed under [MIT](LICENSE).
