"""Sensor interface for the 12V DC ReGrid bus.

The module uses MCP3008 ADC channels:
- channel 0: bus voltage sensor or divider output
- channel 1: ACS712 current sensor output

When Raspberry Pi ADC libraries are not installed, it runs in dry-run mode.
"""

from collections import deque

from config import FILTER_WINDOW_SIZE, NOMINAL_VOLTAGE

try:
    import board
    import busio
    import digitalio
    import adafruit_mcp3xxx.mcp3008 as MCP
    from adafruit_mcp3xxx.analog_in import AnalogIn
except ImportError:
    board = None
    busio = None
    digitalio = None
    MCP = None
    AnalogIn = None


ADC_VREF = 3.3
VOLTAGE_DIVIDER_RATIO = 5.0
ACS712_SENSITIVITY = 0.185
ACS712_OFFSET = 1.65
SAMPLES = 20

_voltage_window = deque(maxlen=FILTER_WINDOW_SIZE)
_current_window = deque(maxlen=FILTER_WINDOW_SIZE)

_spi = None
_mcp = None
_channel_voltage = None
_channel_current = None


def _init_mcp3008():
    global _spi, _mcp, _channel_voltage, _channel_current

    if _mcp is not None or board is None:
        return

    try:
        _spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
        cs = digitalio.DigitalInOut(board.D8)
        _mcp = MCP.MCP3008(_spi, cs)
        _channel_voltage = AnalogIn(_mcp, MCP.P0)
        _channel_current = AnalogIn(_mcp, MCP.P1)
        print("[SENSOR] MCP3008 ADC initialized")
    except Exception as exc:
        print(f"[SENSOR] MCP3008 initialization failed: {exc}")


def _average_channel_voltage(channel, samples=SAMPLES):
    values = []
    for _ in range(samples):
        values.append(channel.voltage)
    return sum(values) / len(values)


def read_voltage():
    """Read the 12V DC bus voltage."""
    _init_mcp3008()

    if _channel_voltage is None:
        print("[DRY-RUN] Voltage sensor is not available")
        return NOMINAL_VOLTAGE

    try:
        adc_voltage = _average_channel_voltage(_channel_voltage)
        return max(adc_voltage * VOLTAGE_DIVIDER_RATIO, 0.0)
    except Exception as exc:
        print(f"[SENSOR] Voltage read error: {exc}")
        return NOMINAL_VOLTAGE


def read_current():
    """Read DC current from the ACS712 sensor."""
    _init_mcp3008()

    if _channel_current is None:
        print("[DRY-RUN] Current sensor is not available")
        return 2.0

    try:
        adc_voltage = _average_channel_voltage(_channel_current)
        current = (adc_voltage - ACS712_OFFSET) / ACS712_SENSITIVITY
        return abs(current)
    except Exception as exc:
        print(f"[SENSOR] Current read error: {exc}")
        return 0.0


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
