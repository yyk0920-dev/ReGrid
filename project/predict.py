import joblib
import pandas as pd
import requests

MODEL_PATH = "models/random_forest_model.pkl"

PC_URL = "http://127.0.0.1:8000"

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

def decide_ess_cmd(fault_code):
    """
    ESS 투입 여부 결정.
    지금은 예시로 C상 관련 고장 또는 상위 고장일 때 ESS ON.
    프로젝트 로직에 맞게 수정 가능.
    """
    if fault_code in [3, 4, 7]:
        return 1
    else:
        return 0

def send_ess_cmd(cmd):
    try:
        r = requests.post(f"{PC_URL}/ess/{cmd}", timeout=2)
        print("ESS 요청 결과:", r.status_code, r.text)
    except Exception as e:
        print("ESS 요청 실패:", e)

def predict_fault(Ia, Ib, Ic, temperature, spark_detected):
    input_data = pd.DataFrame([{
        "Ia": Ia,
        "Ib": Ib,
        "Ic": Ic,
        "temperature": temperature,
        "spark_detected": spark_detected
    }])

    fault_code = int(model.predict(input_data)[0])
    fault_name = fault_names.get(fault_code, "UNKNOWN")

    ess_cmd = decide_ess_cmd(fault_code)

    print("예측 고장 코드:", fault_code)
    print("예측 고장 이름:", fault_name)
    print("ESS 명령:", ess_cmd)

    send_ess_cmd(ess_cmd)

    return fault_code, ess_cmd

if __name__ == "__main__":
    # 테스트 예시
    predict_fault(
        Ia=6.5,
        Ib=1.0,
        Ic=6.8,
        temperature=35,
        spark_detected=0
    )