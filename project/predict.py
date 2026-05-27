import joblib
import pandas as pd

# =========================
# 모델 경로
# =========================

MODEL_PATH = "models/random_forest_fault_classifier.pkl"
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
# 예측 결과 외부 전송
# =========================

def send_fault_code(fault_code):
    """
    예측 결과는 Flask 제어 입력과 연결하지 않는다.

    이 함수는 예전 호출부 호환용으로만 남겨둔다.
    추후 n8n 같은 외부 연동이 필요하면 Flask /preset이 아니라
    별도 이벤트 전송 경로를 만들어 사용한다.
    """
    print("--------------------------------")
    print("Flask 전송 안 함")
    print("예측 fault_code:", fault_code)
    print("예측 결과는 터미널 출력과 반환값으로만 사용합니다.")


# =========================
# AI 고장 유형 분류
# =========================

def predict_fault_type(Ia, Ib, Ic, temperature, spark_detected, send_to_pc=False):
    """
    AI 역할:
    센서값을 입력받아 F1~F9 고장 유형만 분류한다.

    입력:
    Ia: A상 전류
    Ib: B상 전류
    Ic: C상 전류
    temperature: 온도값
    spark_detected: YOLO 스파크 감지 결과, 0 또는 1
    send_to_pc: 호환용 인자. True여도 Flask로 전송하지 않음.

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
        send_to_pc=False
    )
