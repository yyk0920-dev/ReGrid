import os
import socket
import struct
import math

# ==========================================================
# relay.py 함수 사용
# ==========================================================

try:
    from relay import (
        cut_main_power,
        restore_main_power,
        switch_to_backup,
        stop_backup,
        cleanup_relays,
    )
    RELAY_AVAILABLE = True
except Exception as e:
    RELAY_AVAILABLE = False
    print(f"[RELAY] relay.py import failed: {e}")
    print("[RELAY] Dry-run mode enabled")

    def cut_main_power():
        print("[DRY-RUN] cut_main_power()")

    def restore_main_power():
        print("[DRY-RUN] restore_main_power()")

    def switch_to_backup():
        print("[DRY-RUN] switch_to_backup()")

    def stop_backup():
        print("[DRY-RUN] stop_backup()")

    def cleanup_relays():
        print("[DRY-RUN] cleanup_relays()")


# ==========================================================
# 환경변수
# ==========================================================

def get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


NODE_ID = get_env("REGRID_NODE_ID", "node-a")
HOST = get_env("REGRID_HOST", "0.0.0.0")
PORT = int(get_env("REGRID_PORT", "5000"))

# A -> B, B -> C 전달용
NEXT_NODE_IP = get_env("REGRID_CHAIN_NEXT_NODE", None)
NEXT_NODE_PORT = int(get_env("REGRID_CHAIN_NEXT_PORT", "5000"))

# Simulink PC로 Circuit Breaker open/close 신호를 다시 보낼 때 사용
SIMULINK_LAPTOP_IP = get_env("REGRID_SIMULINK_IP", None)
SIMULINK_CONTROL_PORT = int(get_env("REGRID_SIMULINK_CONTROL_PORT", "6001"))

BYTE_ORDER = get_env("REGRID_BYTE_ORDER", "big")  # big or little

# Simulink가 여러 float를 보내는 경우 어느 값을 V/I로 쓸지 선택
V_INDEX = int(get_env("REGRID_V_INDEX", "2"))
I_INDEX = int(get_env("REGRID_I_INDEX", "4"))

# 데이터 해석 방식
# auto   : len에 따라 자동 해석
# float  : single float 배열로 해석
# double : double 배열로 해석
DATA_MODE = get_env("REGRID_DATA_MODE", "auto")

# 릴레이 실제 제어 여부
ENABLE_RELAY = int(get_env("REGRID_ENABLE_RELAY", "1"))

# Simulink Circuit Breaker로 0.0/1.0 신호를 다시 보낼지 여부
ENABLE_SIMULINK_CONTROL = int(get_env("REGRID_ENABLE_SIMULINK_CONTROL", "0"))

# 디버그 출력 여부
DEBUG = int(get_env("REGRID_DEBUG", "0"))

# 고장 기준
CURRENT_THRESHOLD = float(get_env("REGRID_CURRENT_THRESHOLD", "5.0"))
DISCONNECT_THRESHOLD = float(get_env("REGRID_DISCONNECT_THRESHOLD", "0.05"))
UNDERVOLTAGE_THRESHOLD = float(get_env("REGRID_UNDERVOLTAGE_THRESHOLD", "10.50"))
OVERVOLTAGE_THRESHOLD = float(get_env("REGRID_OVERVOLTAGE_THRESHOLD", "13.80"))

last_fault = None


# ==========================================================
# Fault 판단
# ==========================================================

def classify_fault(voltage, current):
    """
    fault code:
    0 = NORMAL
    1 = UNDERVOLTAGE
    2 = OVERLOAD
    3 = DISCONNECT
    4 = OVERVOLTAGE
    """

    if not math.isfinite(voltage) or not math.isfinite(current):
        return 3

    voltage_abs = abs(voltage)
    current_abs = abs(current)

    if current_abs < DISCONNECT_THRESHOLD:
        return 3

    if current_abs > CURRENT_THRESHOLD:
        return 2

    if voltage_abs < UNDERVOLTAGE_THRESHOLD:
        return 1

    if voltage_abs > OVERVOLTAGE_THRESHOLD:
        return 4

    return 0


def get_fault_name(fault):
    names = {
        0: "NORMAL",
        1: "UNDERVOLTAGE",
        2: "OVERLOAD",
        3: "DISCONNECT",
        4: "OVERVOLTAGE",
    }
    return names.get(fault, "UNKNOWN")


# ==========================================================
# Simulink Circuit Breaker 제어 신호 송신
# ==========================================================

def send_breaker_signal_to_simulink(closed):
    """
    Simulink Circuit Breaker 제어용 UDP 송신.

    closed=True  -> 1.0 전송, 회로 닫힘
    closed=False -> 0.0 전송, 회로 열림

    Simulink UDP Receive 쪽은 double 1개를 받도록 맞추는 것을 추천.
    """

    if not ENABLE_SIMULINK_CONTROL:
        return

    if not SIMULINK_LAPTOP_IP:
        print("[SIMULINK CTRL] No SIMULINK_LAPTOP_IP configured.")
        return

    value = 1.0 if closed else 0.0

    fmt = ">d" if BYTE_ORDER == "big" else "<d"
    data = struct.pack(fmt, value)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(data, (SIMULINK_LAPTOP_IP, SIMULINK_CONTROL_PORT))
        print(
            f"[SIMULINK CTRL] Sent breaker signal {value:.1f} "
            f"to {SIMULINK_LAPTOP_IP}:{SIMULINK_CONTROL_PORT}"
        )
    except Exception as e:
        print(f"[SIMULINK CTRL ERROR] {e}")
    finally:
        sock.close()


# ==========================================================
# 릴레이 제어
# ==========================================================

def control_relay_by_fault(fault):
    """
    fault == 0:
        메인 릴레이 복구, ESS OFF, Simulink breaker close

    fault != 0:
        메인 릴레이 차단, ESS ON, Simulink breaker open
    """

    global last_fault

    if last_fault == fault:
        return

    last_fault = fault

    if fault == 0:
        print("[CONTROL] NORMAL -> restore main power, stop backup")

        if ENABLE_RELAY:
            restore_main_power()
            stop_backup()

        send_breaker_signal_to_simulink(closed=True)

    else:
        print(f"[CONTROL] FAULT -> cut main power, switch to backup | fault={fault}")

        if ENABLE_RELAY:
            cut_main_power()
            switch_to_backup()

        send_breaker_signal_to_simulink(closed=False)


# ==========================================================
# UDP 데이터 해석
# ==========================================================

def unpack_float_array(data):
    float_count = len(data) // 4
    usable_len = float_count * 4

    prefix = ">" if BYTE_ORDER == "big" else "<"
    fmt = prefix + ("f" * float_count)

    values = struct.unpack(fmt, data[:usable_len])
    return values


def unpack_double_array(data):
    double_count = len(data) // 8
    usable_len = double_count * 8

    prefix = ">" if BYTE_ORDER == "big" else "<"
    fmt = prefix + ("d" * double_count)

    values = struct.unpack(fmt, data[:usable_len])
    return values


def decode_udp_values(data):
    """
    지원 형식:

    1) Simulink single 배열
       예: single [V, I] -> len=8
       예: single 10개 -> len=40

    2) Simulink double 배열
       예: double current 하나 -> len=8
       예: double [V, I] -> len=16

    DATA_MODE=auto일 때:
       - len=40이면 float 배열로 해석
       - len=16이면 double [V,I]로 해석
       - len=8이면 우선 float [V,I]로 해석하되 값이 이상하면 double current로도 볼 수 있음
    """

    if len(data) < 8:
        raise ValueError(f"packet too short: len={len(data)}")

    values = None

    if DATA_MODE == "float":
        values = unpack_float_array(data)

    elif DATA_MODE == "double":
        values = unpack_double_array(data)

    else:
        # auto mode
        if len(data) == 40:
            values = unpack_float_array(data)

        elif len(data) == 16:
            values = unpack_double_array(data)

        elif len(data) == 8:
            # 기본은 single [V,I]
            values = unpack_float_array(data)

        else:
            # 애매하면 float 배열로 우선 해석
            if len(data) % 4 == 0:
                values = unpack_float_array(data)
            elif len(data) % 8 == 0:
                values = unpack_double_array(data)
            else:
                raise ValueError(f"unsupported packet length: {len(data)}")

    if not values:
        raise ValueError("no values decoded")

    # double current 하나만 들어온 경우
    if len(values) == 1:
        voltage = 12.0
        current = float(values[0])
        return voltage, current, values

    # single [V, I] 또는 double [V, I]만 들어온 경우
    if len(values) == 2:
        voltage = float(values[0])
        current = float(values[1])
        return voltage, current, values

    if V_INDEX >= len(values) or I_INDEX >= len(values):
        raise ValueError(
            f"index out of range. len(values)={len(values)}, "
            f"V_INDEX={V_INDEX}, I_INDEX={I_INDEX}"
        )

    voltage = float(values[V_INDEX])
    current = float(values[I_INDEX])

    return voltage, current, values


# ==========================================================
# 다음 노드로 전달
# ==========================================================

def send_values_to_next_node(voltage, current):
    """
    다음 RPi로 V, I만 전달.
    전달 형식은 single [V, I], 총 8바이트.
    """

    if not NEXT_NODE_IP:
        print(f"[{NODE_ID}] No next node. Stop forwarding.")
        return

    fmt = ">ff" if BYTE_ORDER == "big" else "<ff"
    data = struct.pack(fmt, float(abs(voltage)), float(abs(current)))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(data, (NEXT_NODE_IP, NEXT_NODE_PORT))
        print(
            f"[CHAIN] {NODE_ID} -> {NEXT_NODE_IP}:{NEXT_NODE_PORT} | "
            f"V={abs(voltage):.2f}, I={abs(current):.2f}"
        )
    except Exception as e:
        print(f"[CHAIN ERROR] failed to send to {NEXT_NODE_IP}:{NEXT_NODE_PORT} | {e}")
    finally:
        sock.close()


# ==========================================================
# 메인 수신 루프
# ==========================================================

def receive_values():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))

    print(f"[{NODE_ID}] UDP listening on {HOST}:{PORT}")
    print(f"[{NODE_ID}] byte_order={BYTE_ORDER}")
    print(f"[{NODE_ID}] data_mode={DATA_MODE}")
    print(f"[{NODE_ID}] V_INDEX={V_INDEX}, I_INDEX={I_INDEX}")
    print(f"[{NODE_ID}] relay_available={RELAY_AVAILABLE}, enable_relay={ENABLE_RELAY}")
    print(f"[{NODE_ID}] enable_simulink_control={ENABLE_SIMULINK_CONTROL}")
    print(f"[{NODE_ID}] debug={DEBUG}")

    if ENABLE_RELAY:
        print("[INIT] restore main power, stop backup")
        restore_main_power()
        stop_backup()

    while True:
        data, addr = sock.recvfrom(4096)

        try:
            voltage, current, values = decode_udp_values(data)
        except Exception as e:
            print(f"[ERROR] decode failed from {addr}: {e}")
            if DEBUG:
                print(f"[DEBUG] len={len(data)}, raw={data.hex()}")
            continue

        if DEBUG:
            preview = ", ".join([f"{v:.3f}" for v in values[:12]])
            print(f"[DEBUG] from {addr} | len={len(data)} | raw={data[:40].hex()}")
            print(f"[DEBUG] decoded values: [{preview}]")

        voltage = abs(voltage)
        current = abs(current)

        fault = classify_fault(voltage, current)
        fault_name = get_fault_name(fault)

        print(
            f"[{NODE_ID}] from {addr} | "
            f"V={voltage:.2f} V, I={current:.2f} A, "
            f"FAULT={fault}({fault_name})"
        )

        control_relay_by_fault(fault)

        if NODE_ID in ("node-a", "node-b"):
            send_values_to_next_node(voltage, current)


def main():
    print("===================================")
    print("ReGrid UDP VI Receiver / Relay / Forwarder")
    print(f"NODE_ID={NODE_ID}")
    print(f"HOST={HOST}")
    print(f"PORT={PORT}")
    print(f"NEXT_NODE_IP={NEXT_NODE_IP}")
    print(f"NEXT_NODE_PORT={NEXT_NODE_PORT}")
    print(f"SIMULINK_LAPTOP_IP={SIMULINK_LAPTOP_IP}")
    print(f"SIMULINK_CONTROL_PORT={SIMULINK_CONTROL_PORT}")
    print(f"BYTE_ORDER={BYTE_ORDER}")
    print(f"DATA_MODE={DATA_MODE}")
    print(f"V_INDEX={V_INDEX}")
    print(f"I_INDEX={I_INDEX}")
    print(f"ENABLE_RELAY={ENABLE_RELAY}")
    print(f"ENABLE_SIMULINK_CONTROL={ENABLE_SIMULINK_CONTROL}")
    print(f"DEBUG={DEBUG}")
    print("===================================")

    try:
        receive_values()
    except KeyboardInterrupt:
        print("\n[EXIT] KeyboardInterrupt")
    finally:
        cleanup_relays()


if __name__ == "__main__":
    main()
