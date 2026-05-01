# main.py

import time
from sensor import get_power_data
from fault_detection import detect_fault
from power_control import handle_fault
# from comm import send_message

def main():
    while True:
        voltage, current = get_power_data()

        print(f"전압: {voltage:.2f}V, 전류: {current:.2f}A")

        fault = detect_fault(current)
        print(f"상태: {fault}")

        handle_fault(fault)

        # TODO: 다른 라즈베리파이에 상태 전송
        # send_message(fault, "192.168.0.X")

        time.sleep(1)

if __name__ == "__main__":
    main()