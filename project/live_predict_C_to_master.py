import socket
import struct
import time
from datetime import datetime

import requests

# ==============================
# C RPi 설정
# ==============================

NODE_NAME = "C"

# Simulink에서 C RPi로 전류 UDP 보내는 포트
UDP_HOST = "0.0.0.0"
UDP_PORT = 5000

# A RPi 주소
# 반드시 A 라즈베리파이 IP로 수정
A_RPI_IP = "172.20.10.3"
A_MASTER_URL = f"http://{A_RPI_IP}:9000/peer_decision"

# Simulink UDP Send 데이터 형식
# [Ia, Ib, Ic, temperature, sound]
UDP_STRUCT_FORMAT = "!5f"
UDP_PACKET_SIZE = struct.calcsize(UDP_STRUCT_FORMAT)

# A RPi로 너무 자주 보내지 않도록 전송 간격
SEND_INTERVAL_SEC = 0.3

# 같은 fault_code면 반복 전송 줄이기
SEND_ONLY_ON_CHANGE = False

FAULT_NAMES = {
    0: "NORMAL",
    1: "F1_ABC_SHORT",
    2: "F2_AB_SHORT",
    3: "F3_BC_SHORT",
    4: "F4_CA_SHORT",
    5: "F5_A_GROUND",
    6: "F6_B_GROUND",
    7: "F7_C_GROUND",
    8: "F8_LOAD_OPEN",
    9: "F9_ETC",
}


# ==============================
# AI 예측 함수
# ==============================

def predict_fault_code(Ia, Ib, Ic, temperature, sound):
    """
    임시 예측 함수.

    나중에 기존 live_predict_udp.py에서 쓰던
    model.predict 부분을 여기에 넣으면 된다.

    입력:
    Ia, Ib, Ic, temperature, sound

    출력:
    fault_code
    """

    max_i = max(abs(Ia), abs(Ib), abs(Ic))
    min_i = min(abs(Ia), abs(Ib), abs(Ic))

    # 전류가 거의 없으면 정상
    if max_i < 0.05:
        return 0

    # 임시 기준: 전류가 너무 크면 고장
    if max_i >= 3.0:
        return 1

    # 임시 기준: 상 불균형이 크면 고장
    if max_i - min_i >= 2.0:
        return 4

    return 0


def fault_to_relay_decision(fault_code):
    """
    relay_decision:
    0 = 연결 유지
    1 = 차단 요청
    """

    if 1 <= int(fault_code) <= 9:
        return 1
    return 0


def send_to_a_master(fault_code, Ia, Ib, Ic, temperature, sound):
    relay_decision = fault_to_relay_decision(fault_code)
    fault_name = FAULT_NAMES.get(fault_code, "UNKNOWN")

    payload = {
        "node": NODE_NAME,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fault_code": int(fault_code),
        "fault_name": fault_name,
        "relay_decision": int(relay_decision),
        "currents": {
            "Ia": round(float(Ia), 4),
            "Ib": round(float(Ib), 4),
            "Ic": round(float(Ic), 4),
            "temperature": round(float(temperature), 4),
            "sound": round(float(sound), 4),
        },
    }

    try:
        res = requests.post(A_MASTER_URL, json=payload, timeout=1.0)

        print(
            f"[{NODE_NAME} -> A] status={res.status_code}, "
            f"fault_code={fault_code}({fault_name}), "
            f"relay_decision={relay_decision}",
            flush=True,
        )

        try:
            print(f"[A RESPONSE] {res.json()}", flush=True)
        except Exception:
            print(f"[A RESPONSE TEXT] {res.text}", flush=True)

    except Exception as e:
        print(f"[ERROR] Failed to send to A master: {e}", flush=True)


def main():
    print("====================================", flush=True)
    print(f" ReGrid {NODE_NAME} Node starting", flush=True)
    print(f" UDP current input : {UDP_HOST}:{UDP_PORT}", flush=True)
    print(f" A master URL      : {A_MASTER_URL}", flush=True)
    print(f" UDP format        : {UDP_STRUCT_FORMAT}", flush=True)
    print(f" UDP packet size   : {UDP_PACKET_SIZE} bytes", flush=True)
    print("====================================", flush=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_HOST, UDP_PORT))

    last_send_time = 0.0
    last_fault_code = None

    while True:
        try:
            data, addr = sock.recvfrom(1024)

            if len(data) != UDP_PACKET_SIZE:
                print(
                    f"[UDP WARN] from={addr}, invalid size={len(data)}, "
                    f"expected={UDP_PACKET_SIZE}",
                    flush=True,
                )
                continue

            Ia, Ib, Ic, temperature, sound = struct.unpack(UDP_STRUCT_FORMAT, data)

            fault_code = predict_fault_code(Ia, Ib, Ic, temperature, sound)
            fault_name = FAULT_NAMES.get(fault_code, "UNKNOWN")
            relay_decision = fault_to_relay_decision(fault_code)

            print(
                f"[{NODE_NAME} AI] "
                f"Ia={Ia:.3f}, Ib={Ib:.3f}, Ic={Ic:.3f}, "
                f"temp={temperature:.1f}, sound={sound:.1f} "
                f"=> fault_code={fault_code}({fault_name}), "
                f"relay_decision={relay_decision}",
                flush=True,
            )

            now = time.time()

            should_send = now - last_send_time >= SEND_INTERVAL_SEC

            if SEND_ONLY_ON_CHANGE:
                should_send = should_send and fault_code != last_fault_code

            if should_send:
                send_to_a_master(fault_code, Ia, Ib, Ic, temperature, sound)
                last_send_time = now
                last_fault_code = fault_code

        except KeyboardInterrupt:
            print(f"\n[{NODE_NAME}] stopped by user", flush=True)
            break

        except Exception as e:
            print(f"[ERROR] main loop: {e}", flush=True)
            time.sleep(0.1)


if __name__ == "__main__":
    main()