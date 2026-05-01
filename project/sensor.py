# sensor.py

import random

def read_voltage():
    # TODO: MCP3008 연동
    return random.uniform(210, 230)

def read_current():
    # TODO: ACS712 연동
    return random.uniform(0, 10)

def get_power_data():
    voltage = read_voltage()
    current = read_current()
    return voltage, current