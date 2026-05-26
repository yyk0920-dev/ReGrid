import joblib
import pandas as pd
import requests

# =========================
# 경로 설정
# =========================

MODEL_PATH = "models/random_forest_model.pkl"

# PC에서 Flask app.py가 실행 중인 주소
# 같은 PC에서 테스트하면 127.0.0.1
# 라즈베리파이에서 PC로 보낼 때는 PC IP로 바꿔야 함
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
# ESS 제어 판단 로직
# =========================

def decide_ess_cmd(fault_code):
    """
    RandomForest가 예측한 fault_code를 보고
    ESS를 켤지 말지 결정하는 함수.

    현재 프로젝트 조건:
    - ESS는 C노드 부하 측에만 연결됨
    - ESS는 A-B, B-C 구간으로 전력을 보내는 장치가 아님
    - 따라서 핵심은 고장 유형을 먼저 분류하는 것
    - ESS는 C노드 백업이 필요할 때만 ON

    임시 기준:
    F3 = B-C 단락
    F4 = C-A 단락
    F7 = C상 지락

    위 고장은 C상/Node C와 관련될 가능성이 있으므로 ESS ON으로 둠.
    실제 회로 기준에 따라 나중에 수정 가능.
    """

    # 기본값: ESS OFF
    ess_cmd = 0

    # C노드 백업이 필요하다고 보는 고장
    if fault_code in [3, 4, 7]:
        ess_cmd = 1

    # 과열, 스파크는 ESS 투입보다 경고/차단 우선으로 둠
    elif fault_code in [8, 9]:
        ess_cmd = 0

    # 정상 상태
    elif fault_code == 0:
        ess_cmd = 0

    # 그 외 F1, F2, F5, F6
    else:
        ess_cmd = 0

    return ess_cmd


# =========================
# Flask로 ESS 명령 전송
# =========================

def send_ess_cmd(cmd):
    """
    Flask app.py의 /ess/0 또는 /ess/1 라우트로 명령 전송.
    app.py가 실행 중이어야 정상 동작함.
    """

    cmd = 1 if cmd else 0

    try:
        r = requests.post(f"{PC_URL}/ess/{cmd}", timeout=2)
        print("ESS 요청 결과:", r.status_code, r.text)

    except Exception as e:
        print("ESS 요청 실패:", e)
        print("확인할 것:")
        print("1. app.py가 실행 중인지 확인")
        print("2. Flask 주소가 http://127.0.0.1:8000 맞는지 확인")
        print("3. 라즈베리파이에서 실행한다면 PC_URL을 PC IP로 바꿔야 함")


# =========================
# 고장 예측 함수
# =========================

def predict_fault(Ia, Ib, Ic, temperature, spark_detected, send_to_flask=True):
    """
    센서값을 입력받아서 F1~F9 고장 유형을 예측하고,
    그 결과를 바탕으로 ESS 명령을 결정함.

    입력:
    Ia = A상 전류
    Ib = B상 전류
    Ic = C상 전류
    temperature = 온도값
    spark_detected = YOLO 스파크 감지 결과, 0 또는 1
    send_to_flask = True면 Flask로 ESS 명령 전송
    """

    input_data = pd.DataFrame([{
        "Ia": Ia,
        "Ib": Ib,
        "Ic": Ic,
        "temperature": temperature,
        "spark_detected": spark_detected
    }])

    # RandomForest가 고장 유형 예측
    fault_code = int(model.predict(input_data)[0])
    fault_name = fault_names.get(fault_code, "UNKNOWN")

    # fault_code를 보고 ESS 명령 결정
    ess_cmd = decide_ess_cmd(fault_code)

    print("================================")
    print("입력값")
    print(f"Ia: {Ia}")
    print(f"Ib: {Ib}")
    print(f"Ic: {Ic}")
    print(f"temperature: {temperature}")
    print(f"spark_detected: {spark_detected}")
    print("--------------------------------")
    print("예측 결과")
    print("예측 고장 코드:", fault_code)
    print("예측 고장 이름:", fault_name)
    print("ESS 명령:", ess_cmd)
    print("================================")

    # Flask로 ESS 명령 전송
    if send_to_flask:
        send_ess_cmd(ess_cmd)

    return fault_code, ess_cmd


# =========================
# 단독 실행 테스트
# =========================

if __name__ == "__main__":

    # 테스트 예시 1: C-A 단락에 가까운 값
    predict_fault(
        Ia=6.5,
        Ib=1.0,
        Ic=6.8,
        temperature=35,
        spark_detected=0,
        send_to_flask=True
    )