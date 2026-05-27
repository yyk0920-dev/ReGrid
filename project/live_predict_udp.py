import socket
import struct
import joblib
import pandas as pd
import numpy as np

from collections import deque

MODEL_PATH = "models/random_forest_fault_classifier.pkl"

UDP_IP = "0.0.0.0"
UDP_PORT = 5000

WINDOW_SIZE = 10

# 최근 데이터 저장 버퍼
Ia_buffer = deque(maxlen=WINDOW_SIZE)
Ib_buffer = deque(maxlen=WINDOW_SIZE)
Ic_buffer = deque(maxlen=WINDOW_SIZE)

prev_Ia = None
prev_Ib = None
prev_Ic = None

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

    # float 5개
    if len(data) != 20:
        raise ValueError(
            f"잘못된 데이터 길이: {len(data)} bytes"
        )

    Ia, Ib, Ic, temperature, sound = struct.unpack("!5f", data)

    return Ia, Ib, Ic, temperature, sound


def predict_fault(Ia, Ib, Ic, temperature, sound):

    global prev_Ia
    global prev_Ib
    global prev_Ic

    # ------------------------------------------------
    # 버퍼 저장
    # ------------------------------------------------
    Ia_buffer.append(Ia)
    Ib_buffer.append(Ib)
    Ic_buffer.append(Ic)

    # ------------------------------------------------
    # Rolling Mean
    # ------------------------------------------------
    Ia_mean_10 = np.mean(Ia_buffer)
    Ib_mean_10 = np.mean(Ib_buffer)
    Ic_mean_10 = np.mean(Ic_buffer)

    # ------------------------------------------------
    # Rolling Variance
    # ------------------------------------------------
    Ia_var_10 = np.var(Ia_buffer)
    Ib_var_10 = np.var(Ib_buffer)
    Ic_var_10 = np.var(Ic_buffer)

    # ------------------------------------------------
    # 전류 차이 Feature
    # ------------------------------------------------
    Iab_diff = abs(Ia - Ib)
    Ibc_diff = abs(Ib - Ic)
    Ica_diff = abs(Ic - Ia)

    # ------------------------------------------------
    # 평균 / 불평형
    # ------------------------------------------------
    I_mean = (Ia + Ib + Ic) / 3

    I_unbalance = (
        abs(Ia - I_mean) +
        abs(Ib - I_mean) +
        abs(Ic - I_mean)
    )

    I_sum = Ia + Ib + Ic

    # ------------------------------------------------
    # 변화량 Feature
    # ------------------------------------------------
    if prev_Ia is None:
        dIa = 0
        dIb = 0
        dIc = 0
    else:
        dIa = Ia - prev_Ia
        dIb = Ib - prev_Ib
        dIc = Ic - prev_Ic

    prev_Ia = Ia
    prev_Ib = Ib
    prev_Ic = Ic

    # ------------------------------------------------
    # 모델 입력
    # ------------------------------------------------
    input_data = pd.DataFrame([{
        "Ia": Ia,
        "Ib": Ib,
        "Ic": Ic,

        "temperature": temperature,
        "sound": sound,

        "Iab_diff": Iab_diff,
        "Ibc_diff": Ibc_diff,
        "Ica_diff": Ica_diff,

        "I_mean": I_mean,
        "I_unbalance": I_unbalance,
        "I_sum": I_sum,

        "Ia_mean_10": Ia_mean_10,
        "Ib_mean_10": Ib_mean_10,
        "Ic_mean_10": Ic_mean_10,

        "Ia_var_10": Ia_var_10,
        "Ib_var_10": Ib_var_10,
        "Ic_var_10": Ic_var_10,

        "dIa": dIa,
        "dIb": dIb,
        "dIc": dIc
    }])

    fault_code = int(model.predict(input_data)[0])

    fault_name = fault_names.get(
        fault_code,
        "UNKNOWN"
    )

    return fault_code, fault_name


def main():

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    sock.bind((UDP_IP, UDP_PORT))

    print("================================")
    print("ReGrid 실시간 AI 고장 판단 시작")
    print("UDP 수신:", f"{UDP_IP}:{UDP_PORT}")
    print("MODEL:", MODEL_PATH)
    print("================================")

    while True:

        data, addr = sock.recvfrom(1024)

        try:

            Ia, Ib, Ic, temperature, sound = decode_packet(data)

        except Exception as e:

            print("[WARN] decode 실패:", e)
            continue

        # ----------------------------------------
        # AI 예측
        # ----------------------------------------
        fault_code, fault_name = predict_fault(
            Ia=Ia,
            Ib=Ib,
            Ic=Ic,
            temperature=temperature,
            sound=sound
        )

        print(
            f"from {addr} | "
            f"Ia={Ia:.3f}, "
            f"Ib={Ib:.3f}, "
            f"Ic={Ic:.3f}, "
            f"TEMP={temperature:.2f}, "
            f"SOUND={sound:.2f} "
            f"=> AI 예측: "
            f"{fault_code} ({fault_name})"
        )


if __name__ == "__main__":
    main()
