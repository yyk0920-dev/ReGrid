import os
import socket
import struct

SIMULINK_PC_IP = os.getenv("REGRID_SIMULINK_IP", "192.168.137.165")

# 네가 방금 만든 Simulink UDP Receive4 Local port
SIMULINK_CMD_PORT = int(os.getenv("REGRID_SIMULINK_CMD_PORT", "6008"))

# Simulink UDP Receive4의 Remote port와 맞출 값
LOCAL_SEND_PORT = int(os.getenv("REGRID_LOCAL_SEND_PORT", "7008"))


def send_fault_command(voltage_cmd, fault_code_cmd):
    """
    RPi 또는 Flask에서 Simulink로
    [voltage_cmd, fault_code_cmd] 두 값을 보냄.

    Simulink UDP Receive4 설정:
    Local port: 6008
    Remote port: 7008
    Data size: [2 1]
    Source Data type: single
    Byte order: big-endian
    """

    voltage_cmd = float(voltage_cmd)
    fault_code_cmd = float(fault_code_cmd)

    packet = struct.pack("!2f", voltage_cmd, fault_code_cmd)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # Simulink의 Remote port와 맞추기 위해 송신 포트를 고정
        sock.bind(("0.0.0.0", LOCAL_SEND_PORT))

        sock.sendto(
            packet,
            (SIMULINK_PC_IP, SIMULINK_CMD_PORT)
        )

        print(
            f"[SIM OUTPUT] Sent to Simulink "
            f"{SIMULINK_PC_IP}:{SIMULINK_CMD_PORT} | "
            f"voltage_cmd={voltage_cmd}, "
            f"fault_code_cmd={int(fault_code_cmd)}"
        )

    finally:
        sock.close()


def send_breaker_command(port, breaker_cmd):
    """
    RPi에서 Simulink 차단기용 UDP Receive로
    차단기 명령 1개만 보냄.

    breaker_cmd:
    1.0 = 연결 유지
    0.0 = 차단
    """

    value = float(breaker_cmd)
    packet = struct.pack("!f", value)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(
            packet,
            (SIMULINK_PC_IP, int(port))
        )

        print(
            f"[BREAKER OUTPUT] Sent breaker_cmd={value} "
            f"to {SIMULINK_PC_IP}:{port}"
        )

    finally:
        sock.close()
