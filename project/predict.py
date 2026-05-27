import joblib
import pandas as pd
import requests

# =========================
# 모델 경로
# =========================

MODEL_PATH = "models/random_forest_fault_classifier.pkl"

# =========================
# PC Flask 서버 주소
# =========================
# 같은 PC에서 테스트할 때:
# PC_URL = "http://127.0.0.1:8000"

# 라즈베리파이에서 PC로 보낼 때:
# PC_URL = "http://192.168.137.1:8000"

PC_URL = "http://127.0.0.1:8000"
# =========================
# 고장 코드 이름
# =========================

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
# 모델 로드
# =========================

model = joblib.load(MODEL_PATH)


# =========================
# PC Flask로 fault_code 전송
# =========================

def send_fault_code(fault_code):
    """
    예측된 fault_code를 PC Flask app.py로 전송한다.

    PC app.py에 /preset/<code> 라우트가 있어야 함.
    예:
    http://192.168.137.1:8000/preset/4
    """

    try:
        r = requests.post(f"{PC_URL}/preset/{fault_code}", timeout=2)

        print("--------------------------------")
        print("PC Flask 전송 결과")
        print("status_code:", r.status_code)
        print("response:", r.text)

    except Exception as e:
        print("--------------------------------")
        print("PC Flask 전송 실패:", e)
        print("확인할 것:")
        print("1. PC에서 app.py가 실행 중인지 확인")
        print("2. PC와 RPi가 같은 네트워크에 있는지 확인")
        print("3. PC_URL이 PC IP와 맞는지 확인")
        print("4. app.py에 /preset/<code> 라우트가 있는지 확인")


# =========================
# AI 고장 유형 분류
# =========================

def predict_fault_type(Ia, Ib, Ic, temperature, spark_detected, send_to_pc=True):
    """
    AI 역할:
    센서값을 입력받아 F1~F9 고장 유형만 분류한다.

    입력:
    Ia: A상 전류
    Ib: B상 전류
    Ic: C상 전류
    temperature: 온도값
    spark_detected: YOLO 스파크 감지 결과, 0 또는 1
    send_to_pc: True면 예측된 fault_code를 PC Flask로 전송

    출력:
    fault_code: 0~9
    fault_name: 고장 이름
    """

    input_data = pd.DataFrame([{
        "Ia": Ia,
        "Ib": Ib,
        "Ic": Ic,
        "temperature": temperature,
        "spark_detected": spark_detected
    }])

    fault_code = int(model.predict(input_data)[0])
    fault_name = fault_names.get(fault_code, "UNKNOWN")

    print("================================")
    print("AI 고장 유형 분류 결과")
    print("================================")
    print("입력값")
    print(f"Ia = {Ia}")
    print(f"Ib = {Ib}")
    print(f"Ic = {Ic}")
    print(f"temperature = {temperature}")
    print(f"spark_detected = {spark_detected}")
    print("--------------------------------")
    print("예측 고장 코드:", fault_code)
    print("예측 고장 유형:", fault_name)
    print("================================")

    if send_to_pc:
        send_fault_code(fault_code)

    return fault_code, fault_name


# =========================
# 단독 실행 테스트
# =========================

if __name__ == "__main__":
    # 테스트 예시: C-A 단락에 가까운 값
    predict_fault_type(
        Ia=6.5,
        Ib=1.0,
        Ic=6.8,
        temperature=35,
        spark_detected=0,
        send_to_pc=True
    )