import socket
import struct
import joblib
import pandas as pd
import requests

MODEL_PATH = "models/random_forest_fault_classifier.pkl"

UDP_IP = "0.0.0.0"
UDP_PORT = 5000

# PC에서 테스트할 때는 127.0.0.1
# RPi에서 PC Flask로 보낼 때는 PC IP
PC_URL = "http://192.168.137.1:8000"

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

model = joblib.load(MODEL_PATH)

def decode_packet(data):
    if len(data) != 20:
        raise ValueError(f"잘못된 데이터 길이: {len(data)} bytes, 예상: 20 bytes")

    Ia, Ib, Ic, temperature, sound = struct.unpack("!5f", data)
    return Ia, Ib, Ic, temperature, sound

def predict_fault(Ia, Ib, Ic, temperature, sound):
    input_data = pd.DataFrame([{
        "Ia": Ia,
        "Ib": Ib,
        "Ic": Ic,
        "temperature": temperature,
        "sound": sound
    }])

    fault_code = int(model.predict(input_data)[0])
    fault_name = fault_names.get(fault_code, "UNKNOWN")
    return fault_code, fault_name

def send_fault_code(fault_code):
    try:
        r = requests.post(f"{PC_URL}/preset/{fault_code}", timeout=10)
        print(f"[PC SEND] /preset/{fault_code} status={r.status_code}")
    except Exception as e:
        print("[PC SEND FAIL]", e)

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))

    print("================================")
    print("ReGrid 실시간 AI 고장 유형 판단 시작")
    print("UDP 수신:", f"{UDP_IP}:{UDP_PORT}")
    print("MODEL:", MODEL_PATH)
    print("================================")

    last_fault_code = None

    while True:
        data, addr = sock.recvfrom(1024)

        try:
            Ia, Ib, Ic, temperature, sound = decode_packet(data)
        except Exception as e:
            print("[WARN] decode 실패:", e)
            continue

        fault_code, fault_name = predict_fault(
            Ia=Ia,
            Ib=Ib,
            Ic=Ic,
            temperature=temperature,
            sound=sound
        )

        print(
            f"from {addr} | "
            f"Ia={Ia:.3f}, Ib={Ib:.3f}, Ic={Ic:.3f}, "
            f"TEMP={temperature:.2f}, SOUND={sound:.2f} "
            f"=> AI 예측: {fault_code}({fault_name})"
        )

        # fault_code가 바뀔 때만 PC Flask로 전송
        if fault_code != last_fault_code:
            send_fault_code(fault_code)
            last_fault_code = fault_code

if __name__ == "__main__":
    main()
