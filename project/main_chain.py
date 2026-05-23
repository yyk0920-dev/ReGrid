import os
import json
import socket
import time

from dsp_bridge import DSPBridge


def get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


NODE_ID = get_env("REGRID_NODE_ID", "node-a")
HOST = get_env("REGRID_HOST", "0.0.0.0")
PORT = int(get_env("REGRID_PORT", "5000"))

NEXT_NODE_IP = get_env("REGRID_CHAIN_NEXT_NODE", None)
NEXT_NODE_PORT = int(get_env("REGRID_CHAIN_NEXT_PORT", "5000"))

SERIAL_PORT = get_env("REGRID_SERIAL_PORT", "/dev/serial0")
SERIAL_BAUD = int(get_env("REGRID_SERIAL_BAUD", "115200"))

INPUT_MODE = get_env("REGRID_CHAIN_INPUT_MODE", "sensor")
TEST_VOLTAGE = float(get_env("TEST_VOLTAGE", "12.0"))
TEST_CURRENT = float(get_env("TEST_CURRENT", "1.0"))


def parse_dsp_line(line):
    """
    DSP 문자열 파싱.

    가능한 형식:
    V=12.00,I=1.00,FAULT=0
    NODE_B,V=12.00,I=1.00,FAULT=0
    V=1200,I=100,FAULT=0

    반환:
    {
        "voltage": 12.0,
        "current": 1.0,
        "fault": 0
    }
    """

    if not line:
        return None

    parts = line.strip().split(",")

    result = {}

    for part in parts:
        part = part.strip()

        if "=" not in part:
            continue

        key, value = part.split("=", 1)
        key = key.strip().upper()
        value = value.strip()

        try:
            if key == "V":
                v = float(value)

                # V=1200 같은 정수 x100 형식일 때만 보정
                # V=12.00 같은 소수점 형식은 그대로 사용
                if "." not in value and abs(v) > 100:
                    v = v / 100.0

                result["voltage"] = v

            elif key == "I":
                i = float(value)

                # I=100 같은 정수 x100 형식일 때만 보정
                # I=1.00 같은 소수점 형식은 그대로 사용
                if "." not in value and abs(i) > 50:
                    i = i / 100.0

                result["current"] = i

            elif key == "FAULT":
                result["fault"] = int(float(value))

        except ValueError:
            pass

    if "voltage" not in result or "current" not in result:
        return None

    if "fault" not in result:
        result["fault"] = 0

    return result


def make_message(voltage, current, fault=0, source=None):
    return {
        "type": "VI_DATA",
        "source": source or NODE_ID,
        "voltage": float(voltage),
        "current": float(current),
        "fault": int(fault),
        "timestamp": time.time(),
    }


def send_to_next_node(message):
    if not NEXT_NODE_IP:
        print("[CHAIN] No next node. Stop forwarding.")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data = json.dumps(message).encode("utf-8")
    sock.sendto(data, (NEXT_NODE_IP, NEXT_NODE_PORT))
    sock.close()

    print(f"[CHAIN] Sent to next node {NEXT_NODE_IP}:{NEXT_NODE_PORT} -> {message}")


def receive_message():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))

    print(f"[CHAIN] {NODE_ID} waiting UDP on {HOST}:{PORT}")

    while True:
        data, addr = sock.recvfrom(4096)

        try:
            message = json.loads(data.decode("utf-8"))
        except Exception as e:
            print(f"[ERROR] Invalid JSON from {addr}: {e}")
            continue

        print(f"[CHAIN] Received from {addr}: {message}")
        return message


def run_node_a():
    """
    A 노드:
    DSP A에서 V/I/FAULT를 읽고 B 노드로 전달.
    """

    print("[START] node-a mode")
    print(f"[INFO] INPUT_MODE={INPUT_MODE}")

    dsp = DSPBridge(SERIAL_PORT, SERIAL_BAUD)

    while True:
        if INPUT_MODE == "test":
            voltage = TEST_VOLTAGE
            current = TEST_CURRENT
            fault = 0

            message = make_message(voltage, current, fault, source="node-a")
            send_to_next_node(message)

            time.sleep(1.0)
            continue

        line = dsp.read_response()

        if not line:
            continue

        print(f"[DSP A] {line}")

        parsed = parse_dsp_line(line)

        if not parsed:
            print("[WARN] DSP line parse failed")
            continue

        message = make_message(
            parsed["voltage"],
            parsed["current"],
            parsed["fault"],
            source="node-a"
        )

        send_to_next_node(message)


def run_middle_node():
    """
    B 노드:
    A에서 받은 V/I를 DSP B로 전달.
    DSP B 응답을 읽고 C 노드로 전달.
    """

    print(f"[START] {NODE_ID} middle mode")

    dsp = DSPBridge(SERIAL_PORT, SERIAL_BAUD)

    while True:
        message = receive_message()

        if message.get("type") != "VI_DATA":
            print("[WARN] Unknown message type")
            continue

        voltage = float(message["voltage"])
        current = float(message["current"])

        print(f"[{NODE_ID}] Input V={voltage}, I={current}")

        dsp_response = dsp.process_vi(voltage, current)

        if dsp_response:
            print(f"[DSP {NODE_ID}] {dsp_response}")
            parsed = parse_dsp_line(dsp_response)
        else:
            print(f"[WARN] No DSP response from {NODE_ID}")
            parsed = None

        if parsed:
            next_message = make_message(
                parsed["voltage"],
                parsed["current"],
                parsed["fault"],
                source=NODE_ID
            )
        else:
            # DSP 응답이 없으면 받은 값을 그대로 다음 노드로 전달
            next_message = make_message(
                voltage,
                current,
                message.get("fault", 0),
                source=NODE_ID
            )

        send_to_next_node(next_message)


def run_last_node():
    """
    C 노드:
    B에서 받은 V/I를 DSP C로 전달하고 최종 결과 출력.
    """

    print(f"[START] {NODE_ID} last mode")

    dsp = DSPBridge(SERIAL_PORT, SERIAL_BAUD)

    while True:
        message = receive_message()

        if message.get("type") != "VI_DATA":
            print("[WARN] Unknown message type")
            continue

        voltage = float(message["voltage"])
        current = float(message["current"])

        print(f"[{NODE_ID}] Final input V={voltage}, I={current}")

        dsp_response = dsp.process_vi(voltage, current)

        if dsp_response:
            print(f"[DSP {NODE_ID}] {dsp_response}")
            parsed = parse_dsp_line(dsp_response)

            if parsed:
                print(
                    f"[FINAL] V={parsed['voltage']:.2f}, "
                    f"I={parsed['current']:.2f}, "
                    f"FAULT={parsed['fault']}"
                )
        else:
            print(f"[WARN] No DSP response from {NODE_ID}")


def main():
    print("===================================")
    print("ReGrid Chain Mode")
    print(f"NODE_ID={NODE_ID}")
    print(f"HOST={HOST}")
    print(f"PORT={PORT}")
    print(f"NEXT_NODE_IP={NEXT_NODE_IP}")
    print(f"SERIAL_PORT={SERIAL_PORT}")
    print("===================================")

    if NODE_ID == "node-a":
        run_node_a()
    elif NODE_ID == "node-b":
        run_middle_node()
    elif NODE_ID == "node-c":
        run_last_node()
    else:
        print(f"[ERROR] Unknown NODE_ID: {NODE_ID}")


if __name__ == "__main__":
    main()