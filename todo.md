- Checks for duplicates
- Check if dc_charger_start_stop is getting right state. running state?
- Recheck availability all sensors each 360th check. This is 30 min whith default interval of 5s.


# Changes in 2.7

# This calculated sensors are deprecated as of 2.7:
- ( plant_daily_pv_energy )

# This are missing yet:
- plant_pv_daily_total_generation 

# This have been added:
- plant_total_generation_of_third_party_inverter
- plant_smart_load_XX_power ( where XX is 1 - 24 )
- plant_smart_load_XX_total_consumption ( where XX is 1 - 24 )
- plant_total_discharged_energy_of_the_evdc
- plant_total_charged_energy_of_the_evdc
- plant_total_generation_of_self_pv
- plant_total_charged_energy_of_the_evac
- plant_total_energy_output_of_oil_fueled_generator
- 

# This sensors have moved from calculated to static sensors, regaining history
- plant_accumulated_battery_charge_energy
- plant_accumulated_battery_discharge_energy
- plant_accumulated_consumed_energy
- plant_daily_consumed_energy
- plant_accumulated_pv_energy
- plant_accumulated_grid_export_energy
- plant_accumulated_grid_import_energy
