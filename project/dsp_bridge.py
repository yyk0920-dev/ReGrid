import struct
import serial


class DSPBridge:
    def __init__(self, port="/dev/serial0", baudrate=115200, timeout=1.0):
        self.ser = serial.Serial(port, baudrate, timeout=timeout)

    def send_vi(self, voltage, current):
        """
        voltage, current를 x100 정수로 바꿔서 DSP에 4바이트 전송.

        packet 구조:
        [voltage low][voltage high][current low][current high]
        """

        voltage_x100 = int(round(float(voltage) * 100))
        current_x100 = int(round(float(current) * 100))

        packet = struct.pack("<HH", voltage_x100, current_x100)
        self.ser.write(packet)

    def read_response(self):
        """
        DSP에서 오는 문자열 한 줄 읽기.
        예:
        NODE_B,V=12.00,I=1.00,FAULT=0
        """

        line = self.ser.readline()

        if not line:
            return None

        return line.decode("utf-8", errors="ignore").strip()

    def process_vi(self, voltage, current):
        """
        V/I를 DSP로 보내고 DSP 응답 문자열을 받음.
        """

        self.send_vi(voltage, current)
        return self.read_response()