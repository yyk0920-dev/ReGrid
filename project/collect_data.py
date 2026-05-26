import os
import csv
import time
import socket
import struct
import argparse
from datetime import datetime

# =========================
# 기본 설정
# =========================

DATA_PATH = "data/regrid_real_data.csv"

# Simulink 또는 PC에서 RPi로 보내는 UDP 포트
# 네 로그에서 node-a가 받고 있던 포트가 보통 5000이면 그대로 사용
UDP_IP = "0.0.0.0"
UDP_PORT = 5000

fault_names = {
    0: "NORMAL / 정상",
    1: "F1 / 3상 단락",
    2: "F2 / A-B 단락",
    3: "F3 / B-C 단락",
    4: "F4 / C-A 단락",
    5: "F5 / A상 지락",
    6: "F6 / B상 지락",
    7: "F7 / C상 지락",
    8: "F8 / 과열",
    9: "F9 / 스파크"
}


# =========================
# CSV 초기화
# =========================

def init_csv():
    os.makedirs("data", exist_ok=True)

    if not os.path.exists(DATA_PATH):
        with open(DATA_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "node_id",
                "Ia",
                "Ib",
                "Ic",
                "temperature",
                "sound",
                "fault_code"
            ])


def save_row(node_id, Ia, Ib, Ic, temperature, sound, fault_code):
    with open(DATA_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().isoformat(),
            node_id,
            Ia,
            Ib,
            Ic,
            temperature,
            sound,
            fault_code
        ])


# =========================
# UDP 데이터 수신
# =========================

def decode_packet(data):
    """
    Simulink/PC에서 float 5개를 UDP로 보낸다고 가정.

    로그 기준:
    len=20
    decoded values: [2.980, 1.212, 1.900, 25.000, 95.000]

    즉 float 5개:
    Ia, Ib, Ic, temperature, sound
    """

    if len(data) != 20:
        raise ValueError(f"잘못된 데이터 길이: {len(data)} bytes, 예상: 20 bytes")

    Ia, Ib, Ic, temperature, sound = struct.unpack("!5f", data)

    return Ia, Ib, Ic, temperature, sound


def collect_data(node_id, fault_code, count, delay):
    fault_name = fault_names.get(fault_code, "UNKNOWN")

    init_csv()

    print("================================")
    print("ReGrid 학습 데이터 수집 시작")
    print("node_id:", node_id)
    print("fault_code:", fault_code)
    print("fault_name:", fault_name)
    print("save_path:", DATA_PATH)
    print("udp_bind:", f"{UDP_IP}:{UDP_PORT}")
    print("count:", count)
    print("================================")
    print("PC GUI에서 해당 고장 버튼을 먼저 누른 뒤 기다리면 CSV에 저장됨.")
    print("중단하려면 Ctrl + C")
    print("================================")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))

    saved_count = 0

    try:
        while True:
            data, addr = sock.recvfrom(1024)

            try:
                Ia, Ib, Ic, temperature, sound = decode_packet(data)

            except Exception as e:
                print("[WARN] 패킷 해석 실패:", e)
                print("[WARN] raw:", data.hex())
                continue

            save_row(
                node_id=node_id,
                Ia=Ia,
                Ib=Ib,
                Ic=Ic,
                temperature=temperature,
                sound=sound,
                fault_code=fault_code
            )

            saved_count += 1

            print(
                f"[{saved_count}] from {addr} | "
                f"Ia={Ia:.3f} A, "
                f"Ib={Ib:.3f} A, "
                f"Ic={Ic:.3f} A, "
                f"TEMP={temperature:.2f}, "
                f"SOUND={sound:.2f}, "
                f"LABEL={fault_code}({fault_name})"
            )

            if count > 0 and saved_count >= count:
                print("================================")
                print("수집 완료")
                print("저장 파일:", DATA_PATH)
                print("저장 개수:", saved_count)
                print("================================")
                break

            if delay > 0:
                time.sleep(delay)

    except KeyboardInterrupt:
        print()
        print("사용자 중단")
        print("현재까지 저장 개수:", saved_count)
        print("저장 파일:", DATA_PATH)

    finally:
        sock.close()


# =========================
# 실행부
# =========================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ReGrid RPi UDP 학습 데이터 수집기")

    parser.add_argument(
        "--node",
        type=str,
        default="node-a",
        help="노드 이름 예: node-a, node-b, node-c"
    )

    parser.add_argument(
        "--label",
        type=int,
        required=True,
        help="수집할 고장 라벨: 0=NORMAL, 1=F1, 2=F2, ... 7=F7"
    )

    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="저장할 데이터 개수. 0이면 Ctrl+C 전까지 계속 저장"
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="저장 간격 초 단위. 기본값 0"
    )

    args = parser.parse_args()

    collect_data(
        node_id=args.node,
        fault_code=args.label,
        count=args.count,
        delay=args.delay
    )