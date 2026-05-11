# main.py - ReGrid 마이크로그리드 컨트롤러 메인 모듈
# 정상 상태: RPi는 각자 DSP 값만 읽음
# Fault 확정 시: peer RPi에게 FAULT_QUERY 전송 후 단독 고장 여부 판단

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
        print(f"[FAULT-QUERY] from={message.get('request_from', address[0])} reply={reply}")
        if client_socket:
            send_json(client_socket, reply)
        return

    if msg_type == "STATUS":
        print(f"[STATUS] peer={message.get('from', address[0])} state={message.get('state')}")
        return

    print(f"[COMM] Unknown peer message from {address[0]}: {message}")


def query_peer_faults(local_fault):
    """Fault 발생 시점에만 peer들에게 현재 고장 여부를 질의."""
    replies = []
    for peer_ip in PEER_NODES:
        query = {
            "type": "FAULT_QUERY",
            "from": NODE_ID,
            "request_from": NODE_ID,
            "local_fault": local_fault,
            "timestamp": time.time(),
        }
        reply = request_response(query, peer_ip)
        if reply is None:
            replies.append({
                "peer_ip": peer_ip,
                "from": peer_ip,
                "state": "UNKNOWN",
                "fault": "NO_RESPONSE",
            })
        else:
            reply["peer_ip"] = peer_ip
            replies.append(reply)
    return replies


def is_single_node_fault(peer_replies):
    """
    내 노드만 Fault인지 판단.
    - 모든 peer가 정상(N)이라고 응답하면 단독 고장으로 판단
    - peer 중 F 또는 UNKNOWN이 있으면 단독 고장으로 확정하지 않음
    """
    if not peer_replies:
        return True
    return all(reply.get("state") == "N" for reply in peer_replies)


def main():
    if not PEER_NODES:
        print("[WARNING] No peer nodes configured. Fault query will run standalone.")

    detector = FaultDetector()
    controller = PowerController()
    start_server_thread(on_message=handle_peer_message)

    previous_fault = "NORMAL"

    while True:
        data = get_power_data()
        voltage = data["voltage"]
        current = data["current"]
        fault = detector.detect(voltage, current)

        with _state_lock:
            global _current_fault, _current_data
            _current_fault = fault
            _current_data = data

        event = build_event(NODE_ID, voltage, current, fault)
        log_and_send(event, NODE_ID)

        # 핵심 변경점:
        # 정상 상태에서는 peer 통신을 하지 않는다.
        # NORMAL -> FAULT로 바뀐 순간에만 다른 RPi에게 상태를 물어본다.
        if previous_fault == "NORMAL" and fault != "NORMAL":
            print(f"[FAULT] Local fault detected at {NODE_ID}: {fault}")
            peer_replies = query_peer_faults(fault)
            print(f"[FAULT] Peer replies: {peer_replies}")

            if is_single_node_fault(peer_replies):
                print(f"[FAULT] Single-node fault confirmed. Isolating {NODE_ID}")
                controller.handle_fault(fault)
            else:
                print("[FAULT] Not isolated as single-node fault. Manual/wider-area logic required.")

        # 복구는 내 DSP 값이 정상으로 돌아왔을 때 수행
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
