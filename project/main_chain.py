import os
import socket
import struct
import math

# relay.py 함수 사용
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


def get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


# =========================
# 기본 설정
# =========================

NODE_ID = get_env("REGRID_NODE_ID", "node-a")
HOST = get_env("REGRID_HOST", "0.0.0.0")
PORT = int(get_env("REGRID_PORT", "5000"))

NEXT_NODE_IP = get_env("REGRID_CHAIN_NEXT_NODE", None)
NEXT_NODE_PORT = int(get_env("REGRID_CHAIN_NEXT_PORT", "5000"))

BYTE_ORDER = get_env("REGRID_BYTE_ORDER", "big")  # big or little

# Simulink가 현재 40바이트를 보내고 있으므로 float 여러 개 중 몇 번째를 쓸지 선택
V_INDEX = int(get_env("REGRID_V_INDEX", "0"))
I_INDEX = int(get_env("REGRID_I_INDEX", "1"))

# 릴레이 동작 여부
ENABLE_RELAY = int(get_env("REGRID_ENABLE_RELAY", "1"))

# 고장 판단 기준
CURRENT_THRESHOLD = float(get_env("REGRID_CURRENT_THRESHOLD", "5.0"))
DISCONNECT_THRESHOLD = float(get_env("REGRID_DISCONNECT_THRESHOLD", "0.05"))
UNDERVOLTAGE_THRESHOLD = float(get_env("REGRID_UNDERVOLTAGE_THRESHOLD", "10.50"))
OVERVOLTAGE_THRESHOLD = float(get_env("REGRID_OVERVOLTAGE_THRESHOLD", "13.80"))

# fault 상태가 반복 출력될 때 릴레이 함수가 계속 호출되는 것 방지
last_fault = None


# =========================
# 고장 판단
# =========================

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

    if current < DISCONNECT_THRESHOLD:
        return 3

    if current > CURRENT_THRESHOLD:
        return 2

    if voltage < UNDERVOLTAGE_THRESHOLD:
        return 1

    if voltage > OVERVOLTAGE_THRESHOLD:
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


def control_relay_by_fault(fault):
    """
    fault == 0 → 메인 릴레이 복구, ESS OFF
    fault != 0 → 메인 릴레이 차단, ESS ON
    """

    global last_fault

    if not ENABLE_RELAY:
        return

    # 같은 fault 상태에서 릴레이 함수를 계속 반복 호출하지 않도록 방지
    if last_fault == fault:
        return

    last_fault = fault

    if fault == 0:
        print("[CONTROL] NORMAL detected -> restore main power, stop backup")
        restore_main_power()
        stop_backup()
    else:
        print(f"[CONTROL] FAULT detected -> cut main power, switch to backup | fault={fault}")
        cut_main_power()
        switch_to_backup()


# =========================
# UDP 데이터 파싱
# =========================

def get_float_array_format(count):
    prefix = ">" if BYTE_ORDER == "big" else "<"
    return prefix + ("f" * count)


def decode_udp_values(data):
    """
    Simulink UDP Send 데이터를 single float 배열로 해석.

    예:
    len=8  -> float 2개
    len=40 -> float 10개

    V_INDEX, I_INDEX로 사용할 전압/전류 위치 선택.
    """

    if len(data) < 8:
        raise ValueError(f"packet too short: len={len(data)}")

    float_count = len(data) // 4
    usable_len = float_count * 4

    fmt = get_float_array_format(float_count)
    values = struct.unpack(fmt, data[:usable_len])

    if V_INDEX >= len(values) or I_INDEX >= len(values):
        raise ValueError(
            f"index out of range. len(values)={len(values)}, "
            f"V_INDEX={V_INDEX}, I_INDEX={I_INDEX}"
        )

    voltage = float(values[V_INDEX])
    current = float(values[I_INDEX])

    return voltage, current, values


# =========================
# UDP 전송
# =========================

def send_values_to_next_node(voltage, current):
    """
    다음 RPi로 V, I 값만 UDP 전송.
    형식: single [V, I], 총 8바이트.
    """

    if not NEXT_NODE_IP:
        print(f"[{NODE_ID}] No next node. Stop forwarding.")
        return

    fmt = ">ff" if BYTE_ORDER == "big" else "<ff"
    data = struct.pack(fmt, float(voltage), float(current))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (NEXT_NODE_IP, NEXT_NODE_PORT))
        print(
            f"[CHAIN] {NODE_ID} -> {NEXT_NODE_IP}:{NEXT_NODE_PORT} | "
            f"V={voltage:.2f}, I={current:.2f}"
        )
    except Exception as e:
        print(f"[CHAIN ERROR] failed to send to {NEXT_NODE_IP}:{NEXT_NODE_PORT} | {e}")
    finally:
        sock.close()


# =========================
# 메인 수신 루프
# =========================

def receive_values():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))

    print(f"[{NODE_ID}] UDP listening on {HOST}:{PORT}")
    print(f"[{NODE_ID}] byte_order={BYTE_ORDER}")
    print(f"[{NODE_ID}] V_INDEX={V_INDEX}, I_INDEX={I_INDEX}")
    print(f"[{NODE_ID}] relay_available={RELAY_AVAILABLE}, enable_relay={ENABLE_RELAY}")

    # 시작 상태는 정상 연결
    if ENABLE_RELAY:
        print("[INIT] restore main power, stop backup")
        restore_main_power()
        stop_backup()

    while True:
        data, addr = sock.recvfrom(4096)

        print(f"[DEBUG] from {addr} | len={len(data)} | raw={data[:40].hex()}")

        try:
            voltage, current, values = decode_udp_values(data)
        except Exception as e:
            print(f"[ERROR] decode failed from {addr}: {e}")
            continue

        preview = ", ".join([f"{v:.3f}" for v in values[:10]])
        print(f"[DEBUG] decoded floats: [{preview}]")

        fault = classify_fault(voltage, current)
        fault_name = get_fault_name(fault)

        print(
            f"[{NODE_ID}] from {addr} | "
            f"V={voltage:.2f} V, I={current:.2f} A, "
            f"FAULT={fault}({fault_name})"
        )

        # 릴레이 제어
        control_relay_by_fault(fault)

        # 체인 전달
        # node-a: B로 전달
        # node-b: C로 전달
        # node-c: 마지막 노드라 전달 안 함
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
    print(f"BYTE_ORDER={BYTE_ORDER}")
    print(f"V_INDEX={V_INDEX}")
    print(f"I_INDEX={I_INDEX}")
    print(f"ENABLE_RELAY={ENABLE_RELAY}")
    print("===================================")

    try:
        receive_values()
    except KeyboardInterrupt:
        print("\n[EXIT] KeyboardInterrupt")
    finally:
        cleanup_relays()


if __name__ == "__main__":
    main()
