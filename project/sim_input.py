"""UDP simulation input reader for ReGrid.

This module lets the Raspberry Pi receive simulated Vrms/Irms values from a PC GUI
or MATLAB/Simulink model. It does not replace the real DSP/SCI sensor path; it is
used only when REGRID_INPUT_MODE=udp.
"""

import socket
import struct
import time

from config import (
    SIM_UDP_BYTE_ORDER,
    SIM_UDP_HOST,
    SIM_UDP_PORT,
    SIM_UDP_TIMEOUT_SEC,
)


class UdpSimulationInput:
    def __init__(
        self,
        host=SIM_UDP_HOST,
        port=SIM_UDP_PORT,
        timeout=SIM_UDP_TIMEOUT_SEC,
        byte_order=SIM_UDP_BYTE_ORDER,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.byte_order = byte_order if byte_order in (">", "<") else ">"
        self.sock = None
        self.last_data = {
            "voltage": 0.0,
            "current": 0.0,
            "source": "udp-sim",
            "timestamp": 0.0,
        }

    def open(self):
        if self.sock is not None:
            return

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(self.timeout)
        print(
            f"[SIM-UDP] Listening on {self.host}:{self.port} "
            f"byte_order={self.byte_order!r}"
        )

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def read(self):
        self.open()

        try:
            data, addr = self.sock.recvfrom(1024)
        except socket.timeout:
            print("[SIM-UDP] Timeout waiting for simulated data; using last value")
            return self.last_data

        if len(data) < 8:
            print(f"[SIM-UDP] Packet too short: {len(data)} bytes")
            return self.last_data

        try:
            voltage, current = struct.unpack(self.byte_order + "ff", data[:8])
        except struct.error as exc:
            print(f"[SIM-UDP] Unpack failed: {exc}")
            return self.last_data

        self.last_data = {
            "voltage": float(voltage),
            "current": float(current),
            "source": "udp-sim",
            "timestamp": time.time(),
            "sender": f"{addr[0]}:{addr[1]}",
        }

        return self.last_data


_udp_input = UdpSimulationInput()


def get_sim_power_data():
    return _udp_input.read()


def close_sim_input():
    _udp_input.close()