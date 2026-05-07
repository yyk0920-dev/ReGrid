# main.py - ReGrid 마이크로그리드 컨트롤러 메인 모듈
# 센서 모니터링 → 결함 감지 → 전력 제어 → 로깅/통신 통합 제어

import time

from comm import broadcast_message, start_server_thread
from config import NODE_ID, PEER_NODES, SAMPLE_INTERVAL_SEC
from data_logger import build_event, log_and_send
from fault_detection import FaultDetector
from power_control import PowerController, emergency_shutdown
from relay import cleanup_relays
from sensor import get_power_data


def handle_peer_message(message, address, client_socket=None):
    """
    피어 노드로부터 수신한 메시지를 처리합니다.
    
    Args:
        message (dict): 수신된 메시지
        address (tuple): 송신자 주소 (IP, port)
        client_socket (socket): 클라이언트 소켓 (선택사항)
    """
    fault = message.get("fault")
    node_id = message.get("node_id", address[0])
    if fault and fault != "NORMAL":
        print(f"Peer fault reported by {node_id}: {fault}")


def main():
    # 설정 검증 및 경고
    if not PEER_NODES:
        print("[WARNING] No peer nodes configured. Running in standalone mode.")
    
    detector = FaultDetector()
    controller = PowerController()
    start_server_thread(on_message=handle_peer_message)

    while True:
        data = get_power_data()
        voltage = data["voltage"]
        current = data["current"]

        fault = detector.detect(voltage, current)
        controller.handle_fault(fault)

        event = build_event(NODE_ID, voltage, current, fault)
        log_success, send_success = log_and_send(event, NODE_ID)
        
        # 결함 상태일 때는 ACK 요구
        require_ack = fault != "NORMAL"
        broadcast_message(event, PEER_NODES, require_ack=require_ack)

        print(
            f"[{NODE_ID}] voltage={voltage:.2f}V "
            f"current={current:.2f}A fault={fault}"
        )

        time.sleep(SAMPLE_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopping microgrid controller")
        emergency_shutdown()
    finally:
        cleanup_relays()
