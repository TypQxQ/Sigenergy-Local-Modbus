# Plan: Add Inverter Accumulated PV Energy Production Entity

## Overview
Add a new calculated sensor for tracking total accumulated PV energy production from the inverter. This will integrate the inverter's PV power over time to calculate total energy production.

## Implementation Details

### 1. Add New Calculation Function
Add `calculate_inverter_accumulated_energy` function to `SigenergyCalculations` class:
```python
@staticmethod
def calculate_inverter_accumulated_energy(value: Any, 
    coordinator_data: Optional[Dict[str, Any]] = None, 
    extra_params: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Calculate accumulated PV energy production for inverter.
    
    Uses the inverter PV power reading to calculate accumulated energy through
    time integration using the trapezoidal method.
    """
```

Key aspects:
- Use existing "inverter_pv_power" value from coordinator data
- Store historical data in _power_history dictionary with unique key for inverter
- Calculate energy using trapezoidal integration between readings
- Handle time gaps and data validation
- Only accumulate positive power values (when PV is generating)
- Return accumulated energy in kWh

### 2. Add Sensor Description
Add to INVERTER_SENSORS list in SigenergyCalculatedSensors:
```python
SensorEntityDescription(
    key="inverter_accumulated_pv_energy",
    name="Accumulated PV Energy Production", 
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    state_class=SensorStateClass.TOTAL_INCREASING,
    value_fn=SigenergyCalculations.calculate_inverter_accumulated_energy,
    extra_fn_data=True
)
```

Key aspects:
- Uses TOTAL_INCREASING state class for accumulated metrics
- Matches energy units with other energy sensors
- Requires coordinator data for calculations
- No entity category since this is a primary metric

## Next Steps
1. Switch to code mode to implement solution
2. Start with calculation function
3. Add sensor description
4. Test functionality