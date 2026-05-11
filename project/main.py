# main.py - ReGrid 마이크로그리드 컨트롤러 메인 모듈
# 정상 상태: RPi는 각자 DSP 값만 읽음
# Fault 확정 시: peer RPi에게 FAULT_QUERY 전송 후 단독 고장 여부 판단
#
# 테스트 모드 예시:
#   TEST_MODE=1 TEST_VOLTAGE=220 TEST_CURRENT=1.2 python3 main.py
#   TEST_MODE=1 TEST_FAULT=1 TEST_FAULT_TYPE=OVERLOAD TEST_VOLTAGE=220 TEST_CURRENT=5.0 python3 main.py

import os
import threading
import time

from comm import request_response, send_json, start_server_thread
from config import NODE_ID, PEER_NODES, SAMPLE_INTERVAL_SEC
from data_logger import build_event, log_and_send
from fault_detection import FAULT_CODES, FaultDetector
from power_control import PowerController, emergency_shutdown
from relay import cleanup_relays
from sensor import get_power_data

_current_fault = "NORMAL"
_current_data = {"voltage": 0.0, "current": 0.0}
_state_lock = threading.Lock()

TEST_MODE = os.getenv("TEST_MODE") == "1"
TEST_FAULT = os.getenv("TEST_FAULT") == "1"
TEST_FAULT_TYPE = os.getenv("TEST_FAULT_TYPE", "OVERLOAD")
TEST_VOLTAGE = float(os.getenv("TEST_VOLTAGE", "220.0"))
TEST_CURRENT = float(os.getenv("TEST_CURRENT", "1.0"))


def get_local_state():
    with _state_lock:
        return {
            "node_id": NODE_ID,
            "state": "N" if _current_fault == "NORMAL" else "F",
            "fault": _current_fault,
            "fault_code": FAULT_CODES.get(_current_fault, -1),
            "voltage": _current_data.get("voltage", 0.0),
            "current": _current_data.get("current", 0.0),
            "timestamp": time.time(),
        }


def handle_peer_message(message, address, client_socket=None):
    msg_type = message.get("type")

    if msg_type == "FAULT_QUERY":
        local = get_local_state()
        reply = {
            "type": "FAULT_REPLY",
            "from": NODE_ID,
            "state": local["state"],
            "fault": local["fault"],
            "fault_code": local["fault_code"],
            "timestamp": local["timestamp"],
        }

        print(
            f"[FAULT-QUERY] from={message.get('request_from', address[0])} "
            f"reply_state={reply['state']} fault={reply['fault']}"
        )

        if client_socket:
            send_json(client_socket, reply)
        return

    if msg_type == "STATUS":
        print(
            f"[STATUS] peer={message.get('from', address[0])} "
            f"state={message.get('state')}"
        )
        return

    print(f"[COMM] Unknown peer message from {address[0]}: {message}")


def query_peer_faults(local_fault):
    replies = []

    for peer_ip in PEER_NODES:
        query = {
            "type": "FAULT_QUERY",
            "from": NODE_ID,
            "request_from": NODE_ID,
            "local_fault": local_fault,
            "timestamp": time.time(),
        }

        print(f"[FAULT] Sending FAULT_QUERY to {peer_ip}")
        reply = request_response(query, peer_ip)

        if reply is None:
            print(f"[FAULT] No response from {peer_ip}")
            replies.append(
                {
                    "peer_ip": peer_ip,
                    "from": peer_ip,
                    "state": "UNKNOWN",
                    "fault": "NO_RESPONSE",
                }
            )
        else:
            reply["peer_ip"] = peer_ip
            print(
                f"[FAULT] Reply from {peer_ip}: "
                f"state={reply.get('state')} fault={reply.get('fault')}"
            )
            replies.append(reply)

    return replies


def is_single_node_fault(peer_replies):
    if not peer_replies:
        return True

    return all(reply.get("state") == "N" for reply in peer_replies)


def get_power_data_for_run():
    """
    실제 모드:
        sensor.get_power_data()로 실제 DSP/sensor 값 읽음

    테스트 모드:
        TEST_MODE=1이면 터미널 환경변수 TEST_VOLTAGE, TEST_CURRENT 값을 사용
    """
    if TEST_MODE:
        return {
            "voltage": TEST_VOLTAGE,
            "current": TEST_CURRENT,
        }

    return get_power_data()


def get_fault_state(detector, voltage, current):
    """
    TEST_FAULT=1이면 강제로 Fault 발생.
    TEST_FAULT가 없으면 voltage/current 기반으로 FaultDetector 사용.
    """
    if TEST_FAULT:
        return TEST_FAULT_TYPE

    return detector.detect(voltage, current)


def main():
    if not PEER_NODES:
        print("[WARNING] No peer nodes configured. Fault query will run standalone.")

    if TEST_MODE:
        print(
            f"[TEST] TEST_MODE enabled. "
            f"voltage={TEST_VOLTAGE}V current={TEST_CURRENT}A"
        )

    if TEST_FAULT:
        print(f"[TEST] TEST_FAULT enabled. Forced fault={TEST_FAULT_TYPE}")

    detector = FaultDetector()
    controller = PowerController()

    start_server_thread(on_message=handle_peer_message)

    previous_fault = "NORMAL"

    while True:
        data = get_power_data_for_run()
        voltage = data["voltage"]
        current = data["current"]

        fault = get_fault_state(detector, voltage, current)

        with _state_lock:
            global _current_fault, _current_data
            _current_fault = fault
            _current_data = data

        event = build_event(NODE_ID, voltage, current, fault)
        log_and_send(event, NODE_ID)

        if previous_fault == "NORMAL" and fault != "NORMAL":
            print(f"[FAULT] Local fault detected at {NODE_ID}: {fault}")

            peer_replies = query_peer_faults(fault)
            print(f"[FAULT] Peer replies: {peer_replies}")

            if is_single_node_fault(peer_replies):
                print(f"[FAULT] Single-node fault confirmed. Isolating {NODE_ID}")
                controller.handle_fault(fault)
            else:
                print(
                    "[FAULT] Not isolated as single-node fault. "
                    "Manual/wider-area logic required."
                )

        elif previous_fault != "NORMAL" and fault == "NORMAL":
            print(f"[RECOVERY] Local fault cleared at {NODE_ID}. Restoring relay.")
            controller.handle_fault("NORMAL")

        print(
            f"[{NODE_ID}] voltage={voltage:.2f}V "
            f"current={current:.2f}A fault={fault}"
        )

        previous_fault = fault
        time.sleep(SAMPLE_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopping microgrid controller")
        emergency_shutdown()
    finally:
        cleanup_relays()
