import time

from comm import broadcast_message, start_server_thread
from config import NODE_ID, PEER_NODES, SAMPLE_INTERVAL_SEC
from data_logger import append_jsonl, build_event, send_to_n8n
from fault_detection import FaultDetector
from power_control import PowerController
from relay import cleanup_relays
from sensor import get_power_data


def handle_peer_message(message, address):
    fault = message.get("fault")
    node_id = message.get("node_id", address[0])
    if fault and fault != "NORMAL":
        print(f"Peer fault reported by {node_id}: {fault}")


def main():
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
        append_jsonl(event)
        send_to_n8n(event)
        broadcast_message(event, PEER_NODES)

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
    finally:
        cleanup_relays()
