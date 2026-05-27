#!/usr/bin/env python3
import json
import time
import urllib.request
import urllib.error

# =========================
# 팀원별 설정
# =========================

# 자기 노드 이름
# B팀원이면 "B", C팀원이면 "C"
MY_NODE = "B"

# Flask가 실행 중인 네 노트북 IP
# 네 노트북 ipconfig에서 나온 IPv4 주소로 맞추기
FLASK_PC_IP = "192.168.137.1"
FLASK_PORT = 8000

NODE_DECISION_URL = f"http://{FLASK_PC_IP}:{FLASK_PORT}/node_decision"


# =========================
# Flask로 판단 결과 전송
# =========================
def send_decision(fault_code, relay_decision):
    payload = {
        "node": MY_NODE,
        "fault_code": int(fault_code),
        "relay_decision": int(relay_decision),
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        NODE_DECISION_URL,
        data=data,
        headers={
            "Content-Type": "application/json"
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            result = response.read().decode("utf-8")
            print(f"[SEND OK] {payload}")
            print(f"[FLASK RESPONSE] {result}")
            return True

    except urllib.error.URLError as e:
        print(f"[SEND FAIL] Flask 연결 실패: {e}")
        return False

    except Exception as e:
        print(f"[SEND FAIL] 알 수 없는 오류: {e}")
        return False


# =========================
# 릴레이 판단 로직
# =========================
def decide_relay(fault_code):
    """
    relay_decision 의미:
    0 = 차단 필요 없음 / 연결 유지
    1 = 차단 필요 / 릴레이 개방
    """

    if fault_code >= 1 and fault_code <= 9:
        return 1

    return 0


# =========================
# 임시 수동 입력 모드
# =========================
def manual_mode():
    print("======================================")
    print(f"[Node {MY_NODE}] ReGrid node client started")
    print(f"[Flask URL] {NODE_DECISION_URL}")
    print("fault_code 입력:")
    print("  0 = 정상 / 복구")
    print("  1~7 = 단락/지락 고장")
    print("  8 = 온도 이상")
    print("  9 = 스파크")
    print("  q = 종료")
    print("======================================")

    while True:
        user_input = input("\nfault_code 입력: ").strip()

        if user_input.lower() == "q":
            print("종료")
            break

        try:
            fault_code = int(float(user_input))
        except ValueError:
            print("숫자로 입력해줘")
            continue

        if fault_code < 0:
            fault_code = 0

        if fault_code > 9:
            fault_code = 9

        relay_decision = decide_relay(fault_code)

        print(
            f"[DECISION] node={MY_NODE}, "
            f"fault_code={fault_code}, "
            f"relay_decision={relay_decision}"
        )

        send_decision(fault_code, relay_decision)


# =========================
# 나중에 AI 예측값 연결할 때 쓸 함수
# =========================
def send_ai_result(predicted_fault_code):
    """
    나중에 AI 모델에서 나온 fault_code를 여기에 넣으면 됨.
    예:
        send_ai_result(2)
        send_ai_result(0)
    """

    fault_code = int(predicted_fault_code)
    relay_decision = decide_relay(fault_code)

    return send_decision(fault_code, relay_decision)


# =========================
# 실행부
# =========================
if __name__ == "__main__":
    manual_mode()