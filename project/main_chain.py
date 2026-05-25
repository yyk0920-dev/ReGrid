import os
import socket
import struct
import time


def get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


NODE_ID = get_env("REGRID_NODE_ID", "node-b")
HOST = get_env("REGRID_HOST", "0.0.0.0")
PORT = int(get_env("REGRID_PORT", "5000"))

NEXT_NODE_IP = get_env("REGRID_CHAIN_NEXT_NODE", None)
NEXT_NODE_PORT = int(get_env("REGRID_CHAIN_NEXT_PORT", "5000"))

BYTE_ORDER = get_env("REGRID_BYTE_ORDER", "big")  # big or little


def classify_fault(voltage, current):
    if current < 0.05:
        return 3   # DISCONNECT
    elif current > 5.00:
        return 2   # OVERLOAD
    elif voltage < 10.50:
        return 1   # UNDERVOLTAGE
    elif voltage > 13.80:
        return 4   # OVERVOLTAGE
    return 0       # NORMAL


def make_message(voltage, current, fault=0):
    return {
        "voltage": float(voltage),
        "current": float(current),
        "fault": int(fault),
        "source": NODE_ID,
        "timestamp": time.time(),
    }


def send_values_to_next_node(voltage, current):
    """
    다음 RPi로 V, I 값만 UDP 전송.
    Simulink UDP Send와 같은 형식: single [V, I]
    """

    if not NEXT_NODE_IP:
        return

    fmt = ">ff" if BYTE_ORDER == "big" else "<ff"
    data = struct.pack(fmt, float(voltage), float(current))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(data, (NEXT_NODE_IP, NEXT_NODE_PORT))
    sock.close()

    print(f"[CHAIN] Sent V={voltage:.2f}, I={current:.2f} to {NEXT_NODE_IP}:{NEXT_NODE_PORT}")


def receive_values():
    """
    Simulink UDP Send에서 보내는 single [V, I] 수신.
    Simulink 설정:
    - Source Data Type: single
    - Data size: [1, 2]
    - Byte order: big-endian이면 >ff
    """

    fmt = ">ff" if BYTE_ORDER == "big" else "<ff"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))

    print(f"[{NODE_ID}] UDP listening on {HOST}:{PORT}")
    print(f"[{NODE_ID}] Expecting single [V, I], byte_order={BYTE_ORDER}")

    while True:
        data, addr = sock.recvfrom(1024)

        if len(data) < 8:
            print(f"[WARN] short packet from {addr}: {data}")
            continue

        try:
            voltage, current = struct.unpack(fmt, data[:8])
        except Exception as e:
            print(f"[ERROR] unpack failed from {addr}: {e}, raw={data}")
            continue

        fault = classify_fault(voltage, current)

        print(
            f"[{NODE_ID}] from {addr} | "
            f"V={voltage:.2f} V, I={current:.2f} A, FAULT={fault}"
        )

        # 여기서 릴레이/LED 제어 넣으면 됨
        # fault == 0 → 정상
        # fault != 0 → 고장

        if NODE_ID == "node-b":
            send_values_to_next_node(voltage, current)


def main():
    print("===================================")
    print("ReGrid UDP VI Receiver")
    print(f"NODE_ID={NODE_ID}")
    print(f"HOST={HOST}")
    print(f"PORT={PORT}")
    print(f"NEXT_NODE_IP={NEXT_NODE_IP}")
    print(f"NEXT_NODE_PORT={NEXT_NODE_PORT}")
    print(f"BYTE_ORDER={BYTE_ORDER}")
    print("===================================")

    receive_values()


if __name__ == "__main__":
    main()