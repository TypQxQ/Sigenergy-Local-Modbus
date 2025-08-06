- Checks for duplicates
- Check if dc_charger_start_stop is getting right state. running state?
- Recheck availability all sensors each 360th check. This is 30 min whith default interval of 5s.


# Changes in 2.7

# This calculated sensors are deprecated as of 2.7:
- plant_accumulated_pv_energy
- ( plant_daily_pv_energy )
- plant_accumulated_consumed_energy
- plant_daily_consumed_energy

# This are missing yet:
- plant_pv_daily_total_generation 
- 

# This have been added:
- plant_pv_total_generation
- plant_total_load_consumption
- plant_total_load_daily_consumption
