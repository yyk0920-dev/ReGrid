import socket
import struct
import threading
import time
from datetime import datetime

import requests
from flask import Flask, request, jsonify

# ==============================
# 설정값
# ==============================

# A RPi에서 A노드 전류 UDP 받을 포트
UDP_HOST = "0.0.0.0"
UDP_PORT = 5000

# B/C RPi가 A RPi로 fault_code 보낼 HTTP 서버 포트
PEER_SERVER_HOST = "0.0.0.0"
PEER_SERVER_PORT = 9000

# 노트북 Flask 서버 주소
# 네가 확인한 노트북 IP가 172.20.10.2면 그대로 사용
FLASK_URL = "http://172.20.10.2:8000/master_relay_decision"

# Simulink UDP Send 데이터 형식
# [Ia, Ib, Ic, temperature, sound]
UDP_STRUCT_FORMAT = "!5f"
UDP_PACKET_SIZE = struct.calcsize(UDP_STRUCT_FORMAT)

# Flask로 너무 자주 보내지 않도록 최소 전송 간격
SEND_INTERVAL_SEC = 0.3

# ==============================
# AI 모델 설정
# ==============================

# 지금은 임시 rule 기반 예측 함수로 둔다.
# 나중에 네 기존 live_predict_udp.py의 모델 로딩/예측 부분을
# predict_fault_code() 안에 붙이면 된다.

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
# 전역 상태
# ==============================

state_lock = threading.Lock()

fault_codes = {
    "A": 0,
    "B": 0,
    "C": 0,
}

relay_latch = {
    "A": 0,
    "B": 0,
    "C": 0,
}

last_currents = {
    "A": {
        "Ia": 0.0,
        "Ib": 0.0,
        "Ic": 0.0,
        "temperature": 25.0,
        "sound": 95.0,
    }
}

last_peer_time = {
    "B": None,
    "C": None,
}

last_send_time = 0.0


# ==============================
# Flask 서버: B/C 판단값 수신용
# ==============================

app = Flask(__name__)


@app.route("/peer_decision", methods=["POST"])
def peer_decision():
    """
    B/C RPi가 A RPi로 보내는 JSON 예시:

    {
      "node": "B",
      "fault_code": 4,
      "relay_decision": 1,
      "fault_name": "F4_CA_SHORT"
    }
    """

    try:
        data = request.get_json(force=True)

        node = str(data.get("node", "")).upper()
        fault_code = int(data.get("fault_code", 0))
        relay_decision = int(data.get("relay_decision", 0))
        fault_name = data.get("fault_name", FAULT_NAMES.get(fault_code, "UNKNOWN"))

        if node not in ["B", "C"]:
            return jsonify({
                "ok": False,
                "error": f"invalid node: {node}",
            }), 400

        with state_lock:
            fault_codes[node] = fault_code
            last_peer_time[node] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # latch 핵심
            # 고장이 한 번이라도 들어오면 reset 전까지 relay = 1 유지
            if fault_code >= 1 or relay_decision == 1:
                relay_latch[node] = 1

        print(
            f"[PEER RX] node={node}, fault_code={fault_code}, "
            f"fault_name={fault_name}, relay_decision={relay_decision}",
            flush=True,
        )

        send_master_decision_to_flask()

        return jsonify({
            "ok": True,
            "received": data,
            "fault_codes": fault_codes,
            "relay": relay_latch,
        })

    except Exception as e:
        print(f"[ERROR] /peer_decision: {e}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/reset_latch", methods=["POST", "GET"])
def reset_latch():
    """
    A RPi 내부 latch reset용.
    브라우저나 PowerShell에서:
    http://A_RPI_IP:9000/reset_latch
    """

    with state_lock:
        for node in ["A", "B", "C"]:
            fault_codes[node] = 0
            relay_latch[node] = 0

    print("[RESET LATCH] A/B/C relay latch cleared", flush=True)

    send_master_decision_to_flask()

    return jsonify({
        "ok": True,
        "action": "reset_latch",
        "fault_codes": fault_codes,
        "relay": relay_latch,
    })


@app.route("/state", methods=["GET"])
def get_state():
    with state_lock:
        return jsonify({
            "ok": True,
            "fault_codes": fault_codes,
            "relay": relay_latch,
            "last_currents": last_currents,
            "last_peer_time": last_peer_time,
        })


# ==============================
# A노드 AI 예측 함수
# ==============================

def predict_fault_code(Ia, Ib, Ic, temperature, sound):
    """
    임시 예측 로직.

    나중에 기존 live_predict_udp.py에서 쓰던
    scaler/model/joblib 로딩 후 predict 하는 코드로 교체하면 됨.

    입력:
    Ia, Ib, Ic, temperature, sound

    출력:
    fault_code
    """

    # 완전 임시 기준
    # 실제 AI 모델 연결 전까지 릴레이 흐름 테스트용
    max_i = max(abs(Ia), abs(Ib), abs(Ic))
    min_i = min(abs(Ia), abs(Ib), abs(Ic))

    # 전류가 거의 없으면 정상으로 둠
    if max_i < 0.05:
        return 0

    # 비정상적으로 큰 전류면 A노드 고장으로 테스트
    if max_i >= 3.0:
        return 1

    # 상 불균형이 크면 임시 고장
    if max_i - min_i >= 2.0:
        return 2

    return 0


# ==============================
# relay 판단 함수
# ==============================

def update_a_node_decision(Ia, Ib, Ic, temperature, sound):
    fault_code = predict_fault_code(Ia, Ib, Ic, temperature, sound)
    fault_name = FAULT_NAMES.get(fault_code, "UNKNOWN")

    with state_lock:
        fault_codes["A"] = fault_code

        last_currents["A"] = {
            "Ia": round(float(Ia), 4),
            "Ib": round(float(Ib), 4),
            "Ic": round(float(Ic), 4),
            "temperature": round(float(temperature), 4),
            "sound": round(float(sound), 4),
        }

        # latch 핵심
        if fault_code >= 1:
            relay_latch["A"] = 1

    print(
        f"[A AI] Ia={Ia:.3f}, Ib={Ib:.3f}, Ic={Ic:.3f}, "
        f"temp={temperature:.1f}, sound={sound:.1f} "
        f"=> fault_code={fault_code}({fault_name}), relay_A={relay_latch['A']}",
        flush=True,
    )

    return fault_code


# ==============================
# Flask로 최종 relay 명령 전송
# ==============================

def send_master_decision_to_flask(force=False):
    global last_send_time

    now = time.time()

    if not force and now - last_send_time < SEND_INTERVAL_SEC:
        return

    with state_lock:
        payload = {
            "source": "A_MASTER",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fault_codes": {
                "A": int(fault_codes["A"]),
                "B": int(fault_codes["B"]),
                "C": int(fault_codes["C"]),
            },
            "relay": {
                "A": int(relay_latch["A"]),
                "B": int(relay_latch["B"]),
                "C": int(relay_latch["C"]),
            },
        }

    try:
        res = requests.post(FLASK_URL, json=payload, timeout=1.0)
        last_send_time = now

        print(
            f"[A -> FLASK] status={res.status_code}, payload={payload}",
            flush=True,
        )

        try:
            print(f"[FLASK RESPONSE] {res.json()}", flush=True)
        except Exception:
            print(f"[FLASK RESPONSE TEXT] {res.text}", flush=True)

    except Exception as e:
        print(
            f"[ERROR] Failed to send master decision to Flask: {e}",
            flush=True,
        )


# ==============================
# UDP 수신 루프: A노드 전류 수신
# ==============================

def udp_receive_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_HOST, UDP_PORT))

    print(
        f"[UDP START] A node current receiver listening on {UDP_HOST}:{UDP_PORT}",
        flush=True,
    )
    print(
        f"[UDP FORMAT] {UDP_STRUCT_FORMAT}, packet size={UDP_PACKET_SIZE} bytes",
        flush=True,
    )

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

            update_a_node_decision(Ia, Ib, Ic, temperature, sound)

            send_master_decision_to_flask()

        except Exception as e:
            print(f"[ERROR] UDP receive loop: {e}", flush=True)
            time.sleep(0.1)


# ==============================
# Peer HTTP 서버 실행
# ==============================

def run_peer_server():
    print(
        f"[PEER SERVER START] listening on {PEER_SERVER_HOST}:{PEER_SERVER_PORT}",
        flush=True,
    )
    app.run(
        host=PEER_SERVER_HOST,
        port=PEER_SERVER_PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


# ==============================
# 메인 실행
# ==============================

if __name__ == "__main__":
    print("====================================", flush=True)
    print(" ReGrid A Master Node starting", flush=True)
    print(f" UDP current input   : {UDP_HOST}:{UDP_PORT}", flush=True)
    print(f" Peer HTTP server    : {PEER_SERVER_HOST}:{PEER_SERVER_PORT}", flush=True)
    print(f" Flask gateway URL   : {FLASK_URL}", flush=True)
    print("====================================", flush=True)

    udp_thread = threading.Thread(target=udp_receive_loop, daemon=True)
    udp_thread.start()

    # 시작할 때 일단 Flask에 전체 relay 0 전송
    time.sleep(1.0)
    send_master_decision_to_flask(force=True)

    run_peer_server()
