import serial
import time


FAULT_NORMAL = 0
FAULT_UNDERVOLTAGE = 1
FAULT_OVERLOAD = 2
FAULT_DISCONNECT = 3
FAULT_OVERVOLTAGE = 4


class SensorReader:
    def __init__(self, port="/dev/serial0", baudrate=115200, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None

    def open(self):
        if self.ser is None:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
            time.sleep(1)

    def close(self):
        if self.ser is not None:
            self.ser.close()
            self.ser = None

    def fault_to_text(self, fault_code):
        fault_map = {
            FAULT_NORMAL: "NORMAL",
            FAULT_UNDERVOLTAGE: "UNDERVOLTAGE",
            FAULT_OVERLOAD: "OVERLOAD",
            FAULT_DISCONNECT: "DISCONNECT",
            FAULT_OVERVOLTAGE: "OVERVOLTAGE",
        }

        return fault_map.get(fault_code, "UNKNOWN")

    def parse_line(self, line):
        # Example: V=1050,I=234,FAULT=1
        parts = line.split(",")

        data = {}

        for part in parts:
            key, value = part.split("=")
            data[key.strip()] = int(value.strip())

        voltage_raw = data.get("V", 0)
        current_raw = data.get("I", 0)
        fault = data.get("FAULT", 0)

        return {
            "voltage": voltage_raw / 100.0,
            "current": current_raw / 100.0,
            "voltage_raw": voltage_raw,
            "current_raw": current_raw,
            "fault": fault,
            "fault_text": self.fault_to_text(fault),
        }

    def read(self):
        self.open()

        line = self.ser.readline().decode(errors="ignore").strip()

        if not line:
            return None

        try:
            return self.parse_line(line)
        except Exception as e:
            print(f"[SensorReader] Parse error: line={line}, error={e}")
            return None


if __name__ == "__main__":
    sensor = SensorReader()

    try:
        while True:
            data = sensor.read()

            if data is not None:
                print(data)

    except KeyboardInterrupt:
        print("\nStopped")

    finally:
        sensor.close()

_sensor = SensorReader()

def get_power_data():
    data = _sensor.read()

    if data is None:
        return {
            "voltage": 0.0,
            "current": 0.0,
            "voltage_raw": 0,
            "current_raw": 0,
            "fault": 3,
            "fault_text": "DISCONNECT",
        }

    return data