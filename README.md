# <img src="https://brands.home-assistant.io/sigen/dark_icon.png" alt="Sigenergy" width="50" /> Sigenergy ESS for Home Assistant

[![Stable][releases-shield]][releases] ![Project Maintenance][maintenance-shield] [![GitHub Activity][commits-shield]][commits] [![License][license-shield]](LICENSE)

[releases-shield]: https://img.shields.io/github/release/TypQxQ/Sigenergy-Local-Modbus.svg
[releases]: https://github.com/TypQxQ/Sigenergy-Local-Modbus/releases/latest
[maintenance-shield]: https://img.shields.io/maintenance/yes/2026.svg
[commits-shield]: https://img.shields.io/github/commit-activity/y/TypQxQ/Sigenergy-Local-Modbus.svg
[commits]: https://github.com/TypQxQ/Sigenergy-Local-Modbus/commits/main
[license-shield]: https://img.shields.io/github/license/TypQxQ/Sigenergy-Local-Modbus.svg

Monitor and control your Sigenergy ESS locally over Modbus TCP: solar production,
battery and grid power, energy counters, EMS modes, and AC/DC chargers.
Localized names, status labels and setup dialogs in
[11 languages](docs/migrations/2.0.md#localized-names-and-alarm-details).

> **Before upgrading to 2.0.** Update to **Home Assistant Core 2026.10.0 or
> later**. Eight status sensors use new stored
> values. Check the [migration guide](docs/migrations/2.0.md) for automation
> and template changes.

## Requirements

- Home Assistant Core **2026.10.0 or later**.
- **Modbus TCP enabled** and firmware compatible with your model. SigenStor
  normally requires **SPC109+**; see [model and firmware notes](docs/USAGE.md#model-and-firmware-notes)
  for the SigenMate SPC101 field report.
- Your inverter's IP address and device ID. Reserve its IP address in your router.
- AC chargers normally use ID **1**; assign inverters different IDs when sharing
  the connection. DC chargers are accessed through their inverter.

## Enable Modbus TCP

In the Sigenergy app:

1. Open **Settings**.
2. Select **System Settings**.
3. Open **Modbus TCP Server Settings**.
4. Tick the **Enable** checkbox.

If the menu is unavailable, ask your installer to enable Modbus TCP.

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

Use **Configure → Device settings** to change read-only mode, polling interval
and device settings. **Configure → Display settings** lets you select the entity
name language; **Automatic** follows the Home Assistant server language.
Custom names, entity IDs and history are preserved.

**Controls start in read-only mode.** To use them, disable read-only mode in the
integration options and enable the required control entities in Home Assistant.
Check each setting before changing it. For EMS automation sequencing and device
details, see the [usage guide](docs/USAGE.md).

## Troubleshooting

- Check that Modbus TCP is enabled in the Sigenergy app.
- Check IP address, device IDs, port and firewall rules.
- Download integration diagnostics and capture a debug log before reporting a
  problem: [log guide](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions/54).
- Ask in [Discussions](https://github.com/TypQxQ/Sigenergy-Local-Modbus/discussions)
  or report a bug in [Issues](https://github.com/TypQxQ/Sigenergy-Local-Modbus/issues).

## Dashboards and tools

- [Energy Flow Dashboard](Dashboards/energy_flow_gauge/readme.md)
- [Energy Dashboard setup and sensor mapping](docs/ENERGY_DASHBOARD.md)
- [Recorder advice](docs/USAGE.md#recorder-storage)
- [Energy price and charging chart](Dashboards/nordpool_energy_chart/readme.md)
- [Si-GCG's projects](https://github.com/Si-GCG/sigenergy_projects)
- [EMHASS guide](https://sigenergy.annable.me/)

Contributions welcome: add relevant tests under `tests/` and open a pull request.
Licensed under [MIT](LICENSE).
