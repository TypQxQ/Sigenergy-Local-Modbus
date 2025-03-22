# Sigenergy ESS Integration for Home Assistant

This integration allows you to monitor and control your Sigenergy ESS (Energy Storage System) through Home Assistant. It dynamically discovers and configures your plant, inverters, AC chargers, and DC chargers, providing real-time data and control capabilities.

## Features

- **Dynamic Device Addition and Configuration:** Easily add and configure multiple inverters, AC chargers, and DC chargers within a single plant configuration.
- **Automatic Device Support Detection:** Utilizes Modbus register probing to automatically determine supported features and entities for your specific Sigenergy devices.
- **Real-time Monitoring:** Monitor power flows (grid import/export, battery charge/discharge, PV production), energy statistics (daily/monthly/yearly counters), and battery metrics (SoC, SoH, temperature, cycles).
- **Inverter and Charger Status:** Track the status of your inverters and chargers, including running state, alarms, and detailed parameters.
- **Control Capabilities:** Control your Sigenergy system with options like starting/stopping the plant, changing EMS work modes, and adjusting power limits (availability depends on device model and configuration).

## Requirements

- Home Assistant 2024.4.1 or newer
- Sigenergy ESS with Modbus TCP access
- Network connectivity between Home Assistant and the Sigenergy system
- `pymodbus>=3.0.0`

## Installation

### HACS Installation (Recommended)

1.  Make sure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance.
2.  Add this repository as a custom repository in HACS:
    -   Go to HACS > Integrations
    -   Click the three dots in the top right corner
    -   Select "Custom repositories"
    -   Add the URL of this repository
    -   Select "Integration" as the category
3.  Click "Install" on the Sigenergy integration card.
4.  Restart Home Assistant.

### Manual Installation

1.  Download the latest release from the GitHub repository.
2.  Create a `custom_components/sigen` directory in your Home Assistant configuration directory.
3.  Extract the contents of the release into the `custom_components/sigen` directory.
4.  Restart Home Assistant.

## Configuration

The integration uses a multi-step configuration process through the Home Assistant UI:

1.  Go to Settings > Devices & Services.
2.  Click "Add Integration".
3.  Search for "Sigenergy".
4.  Follow the configuration flow:

    -   **Add a Plant:** The first step is to add a "Plant," which represents your overall Sigenergy system. You'll need to provide the host (IP address) and port (default: 502) of your Sigenergy system. You can also set the integration to read-only mode.
    -   **Add Devices:** After adding a plant, you can add inverters, AC chargers, and DC chargers.  You'll be prompted to select the plant you want to add the device to.
        -   **Inverters:** Provide a slave ID for each inverter. The integration will attempt to connect to the inverter using the plant's host and port, but you can specify different connection details if needed.
        -   **AC Chargers:**  Provide a slave ID for each AC charger. The integration will attempt to connect to the AC charger using the plant's host and port, but you can specify different connection details if needed.
        -   **DC Chargers:** DC Chargers are associated with a specific inverter. You'll select the inverter and the integration will use the inverter's slave ID for the DC charger.

### Configuration Parameters

-   **`host`:** The IP address of your Sigenergy system (required for plant and optionally for individual devices).
-   **`port`:** The Modbus TCP port (default: 502, required for plant and optionally for individual devices).
-   **`inverter_slave_ids`:** A list of slave IDs for your inverters.
-   **`ac_charger_slave_ids`:** A list of slave IDs for your AC chargers.
-   **`dc_charger_slave_ids`:** A list of slave IDs for your DC chargers (these correspond to inverter slave IDs).
-   **`inverter_connections`:** A dictionary mapping inverter names to their connection details (host, port, slave ID).
-   **`ac_charger_connections`:** A dictionary mapping AC charger names to their connection details (host, port, slave ID).
-   **`read_only`:**  Set to `True` to prevent the integration from writing to Modbus registers (recommended for initial setup).

## Entities

The integration dynamically creates entities based on the configured devices (plant, inverters, AC chargers, and DC chargers) and the Modbus registers supported by those devices.

**Common Plant Entities:**

-   Plant Active Power
-   Plant Reactive Power
-   Photovoltaic Power
-   Battery State of Charge (SoC)
-   Battery Power (charging/discharging)
-   Grid Active Power (import/export)
-   EMS Work Mode
-   Plant Running State

**Common Inverter Entities:**

-   Active Power
-   Reactive Power
-   Battery Power
-   Battery SoC
-   Battery Temperature
-   PV Power
-   Grid Frequency
-   Phase Voltages
-   Phase Currents
-   Daily Charge/Discharge Energy

**Common AC Charger Entities:**

-   System State
-   Charging Power
-   Total Energy Consumed

**Common DC Charger Entities:** (These will typically be associated with the corresponding inverter entities)

- DC Charger Power
- DC Charger Status

*Note: The specific entities available will depend on your Sigenergy device models and the Modbus registers they support. The integration uses register probing to automatically discover supported entities.*

## Controls

The integration provides control capabilities, allowing you to manage your Sigenergy system. Common control options include:

-   **Plant Power:** Start/stop the plant.
-   **EMS Work Mode:** Select the desired EMS operating mode (e.g., Max Self Consumption, AI Mode, TOU, Remote EMS).

*Note: More advanced controls are available through Home Assistant's Modbus services. The available controls depend on your device model and configuration.*

## Troubleshooting

### Connection Issues

-   Ensure the IP address and port are correct.
-   Check that the Sigenergy system is powered on and connected to the network.
-   Verify that there are no firewalls blocking the connection.
-   Check the Home Assistant logs for detailed error messages.

### Entity Issues

-   If entities are showing as unavailable, check the connection to the Sigenergy system.
-   If values seem incorrect, verify the Modbus slave IDs are configured correctly.
-   For missing entities, ensure that you have added the corresponding devices (inverters, chargers) during the configuration flow. The integration uses dynamic register probing, so it may take some time to discover all supported entities.

## System Compatibility

This integration has been tested with the following Sigenergy models:

-   SigenStorEC series
-   SigenHybrid series
-   SigenPV series
-   Sigen EV DC Charging Module
-   Sigen EV AC Charger

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This integration is licensed under the MIT License.