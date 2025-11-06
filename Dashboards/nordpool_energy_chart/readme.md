# Energy price chart using nordpool prices.

This is my atempt at a chart displaying todays prices and tomorows prices when available along with imported energy.
- Upper and Lower price threshold is calculated for today+tomorows ***comming*** price.
  - Orange is upper 3:rd of the pricing interval.
  - Green is lower 3:rd of the prices interval.
  - Red is the upper 10% of the pricing interval.

![alt text](HA_energy_chart.jpg)

# Pre-requirements

This has not been tested on HA earlier than 2024.9 and requires the following HACS addons:

- Nordpool - For getting energy prices.
- Apex-Charts card
- Config Template Card Card - For scripting inside the chart card.
- lovelace-card-mod - For styling.

The Nordpool entity should be changed from the default name to just "nordpool" as per the components installation instructions.

# Installation
- Copy - paste the raw card code from `card_nordpool_energy_chart.yaml` to your a new card on you lovelace dashboard. Add any card and replace the raw code that you can edit by clicking on the *SHOW CODE EDITOR* in the bottom left.


# Configuration 

Configure your Nordpool integration.

In the chart-card you should change the following variables to suit your needs:

  - Check so the `nordpool_sensor:` name is correct.
  - The Grid import sensor name: `grid_import_sensor:` should be changed if not using Sigenergy system.
  - The 
  - Change `currency` if needed.
  - `chart_price_cutoff_coerficient` from 0 to 1 to show more or less barheight under the minimum value.
  - `decimals_in_prices` and `decimals_in_energy` to indicate how many decimals to show.
  - `additional_cost_template` is used for adding extra costs if not added by nordpol sensor. It should be empty or start with a arithmetic operator.
    - Example:
    - additional_cost_template: '''*1.25 +(27.2 + 42.8 + 3.04 + 2.19 + 1.4) *1.25'''
    - Ads 25% taxes and then other costs as specified by the grid operator with more taxes.
  - Change the Now text to any label you want to apear `now_text: '''Now'''` besides the line showing current time in the chart.
  - And any of the texts below to suit your language or prefferences.

# Disable the Grid or Battery chart

## Disable the Grid chart

1. Comment out the update entity:
    ```
    entities:
     - ${nordpool_sensor}
     - ${battery_soc_sensor}
     # - ${grid_import_sensor}
   ```

2. Delete the rows starting with  ```- entity: ${grid_import_sensor}``` and all rows under it until the next ```- entity:```. There are currently two such sections.

## Disable the Battery SOC

1. Comment out the update entity:
    ```
    entities:
     - ${nordpool_sensor}
     # - ${battery_soc_sensor}
     - ${grid_import_sensor}
   ```

2. Delete the row starting with  ```- entity: ${battery_soc_sensor}``` and all rows under it until the next ```- entity:```. There is currently only one such section.


# Performance concerns

Most of the calculations are done serverside so it's important to have a good computer.

If the chart flickers and takes long time to load, deactivate updating the chart with each change of battery charge and grid consumption:
```
entities:
  - ${nordpool_sensor}
  # - ${battery_soc_sensor}
  # - ${grid_import_sensor}
```


--

Happy savings! 