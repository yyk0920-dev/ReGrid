import random
from collections import deque

from config import FILTER_WINDOW_SIZE


_voltage_window = deque(maxlen=FILTER_WINDOW_SIZE)
_current_window = deque(maxlen=FILTER_WINDOW_SIZE)


def read_voltage():
    # TODO: Replace with MCP3008 + ZMPT101B conversion.
    return random.uniform(210, 230)


def read_current():
    # TODO: Replace with MCP3008 + ACS712 conversion.
    return random.uniform(0, 10)


def get_power_data():
    raw_voltage = read_voltage()
    raw_current = read_current()

    _voltage_window.append(raw_voltage)
    _current_window.append(raw_current)

    voltage = sum(_voltage_window) / len(_voltage_window)
    current = sum(_current_window) / len(_current_window)

    return {
        "voltage": voltage,
        "current": current,
        "raw_voltage": raw_voltage,
        "raw_current": raw_current,
    }
