import socket
import struct
import time
from pathlib import Path

import cv2
from flask import Flask, jsonify, render_template, request, Response
from ultralytics import YOLO

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent

# ================================
# UDP 설정 1: Flask → Simulink 전원 설정값 전송
# ================================
# Simulink UDP Receive 설정:
# - Local port: 5000
# - Data type: single
# - Data size: [1 2]
# - Byte order: Big Endian
#
# 전송 의미:
# [V_set, I_limit]
#
# V_set   = 메인 전원 전압 설정값
# I_limit = 파워서플라이 전류 제한값 / 과전류 판단 기준값
# ================================
VI_UDP_IP = "127.0.0.1"
VI_UDP_PORT = 5000
vi_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ================================
# UDP 설정 2: Flask → Simulink fault code 전송
# ================================
# Simulink UDP Receive 설정:
# - Local port: 5001
# - Data type: single
# - Data size: [1]
# - Byte order: Big Endian
# ================================
FAULT_UDP_IP = "127.0.0.1"
FAULT_UDP_PORT = 5001
fault_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ================================
# 기본 전원 설정
# ================================
DEFAULT_POWER_VOLTAGE = 12.0
DEFAULT_CURRENT_LIMIT = 1.0

POWER_OFF_VOLTAGE = 0.0
POWER_OFF_CURRENT_LIMIT = 0.0

# ================================
# YOLO 모델 설정
# ================================
try:
    model = YOLO(BASE_DIR / "spark.pt")
    print("✅ spark.pt 모델 로드 완료!")
except Exception as e:
    print(f"⚠️ spark.pt 모델 로드 실패: {e}")
    print("기본 yolov8n.pt 모델로 대체합니다.")
    model = YOLO(BASE_DIR / "yolov8n.pt")

SPARK_COOLDOWN_SEC = 3.0
last_spark_trigger_time = 0.0

# ================================
# 현재 상태
# ================================
current_state = {
    "power": True,
    "code": 0,
    "label": "POWER ON",
    "desc": "정상 전원 인가",
    "voltage": DEFAULT_POWER_VOLTAGE,
    "current": DEFAULT_CURRENT_LIMIT,
    "ai": "대기 중",
    "camera_mode": False,
}

# code: (label, desc)
# 이제 F1~F7은 전압/전류를 바꾸는 버튼이 아니라 고장 스위치 제어 버튼임
faults = {
    0: ("RESET", "고장 해제 / 정상상태"),
    1: ("F1", "3상 단락"),
    2: ("F2", "A-B 단락"),
    3: ("F3", "B-C 단락"),
    4: ("F4", "C-A 단락"),
    5: ("F5", "A상 지락"),
    6: ("F6", "B상 지락"),
    7: ("F7", "C상 지락"),
    8: ("TEMP", "온도 높음"),
    9: ("SPARK", "스파크 감지 / 화재+소리"),
}


def send_power_udp(voltage, current_limit):
    """
    V_set, I_limit을 float32 2개로 UDP 전송.

    Simulink UDP Receive 5000:
    - Data type: single
    - Data size: [1 2]
    """
    msg = struct.pack(">ff", float(voltage), float(current_limit))
    vi_sock.sendto(msg, (VI_UDP_IP, VI_UDP_PORT))


def send_fault_code_udp(code):
    """
    fault code를 float32 1개로 UDP 전송.

    Simulink UDP Receive 5001:
    - Data type: single
    - Data size: [1]
    """
    code = int(code)

    if code < 0 or code > 255:
        raise ValueError(f"fault code must be 0~255, got {code}")

    msg = struct.pack(">f", float(code))
    fault_sock.sendto(msg, (FAULT_UDP_IP, FAULT_UDP_PORT))


def send_all_to_simulink():
    """
    현재 상태의 전원 설정값과 fault_code를 Simulink로 전송.
    """
    send_power_udp(current_state["voltage"], current_state["current"])
    send_fault_code_udp(current_state["code"])

    print(
        f"Sent UDP: power={current_state['power']}, "
        f"code={current_state['code']}, {current_state['label']} / "
        f"V_set={current_state['voltage']}, I_limit={current_state['current']}"
    )


def set_state(code, label, desc, ai=None, send_udp=True):
    """
    fault 상태만 변경.
    전압/전류 제한값은 기존 power 설정 유지.
    """
    current_state["code"] = int(code)
    current_state["label"] = label
    current_state["desc"] = desc

    if ai is not None:
        current_state["ai"] = ai

    if send_udp:
        send_all_to_simulink()


def set_power(on, voltage=None, current_limit=None, ai=None, send_udp=True):
    """
    전원 ON/OFF 제어.
    """
    current_state["power"] = bool(on)

    if on:
        current_state["voltage"] = float(voltage if voltage is not None else DEFAULT_POWER_VOLTAGE)
        current_state["current"] = float(current_limit if current_limit is not None else DEFAULT_CURRENT_LIMIT)
        current_state["code"] = 0
        current_state["label"] = "POWER ON"
        current_state["desc"] = "정상 전원 인가"
    else:
        current_state["voltage"] = POWER_OFF_VOLTAGE
        current_state["current"] = POWER_OFF_CURRENT_LIMIT
        current_state["code"] = 0
        current_state["label"] = "POWER OFF"
        current_state["desc"] = "전원 차단"

    if ai is not None:
        current_state["ai"] = ai

    if send_udp:
        send_all_to_simulink()


def trigger_spark_by_ai():
    """
    YOLO가 스파크를 감지했을 때 SPARK fault_code=9 전송.
    카메라 모드 ON일 때만 동작.
    """
    global last_spark_trigger_time

    if not current_state["camera_mode"]:
        return

    now = time.time()

    if now - last_spark_trigger_time < SPARK_COOLDOWN_SEC:
        return

    last_spark_trigger_time = now

    code = 9
    label, desc = faults[code]

    set_state(
        code=code,
        label=label,
        desc=desc,
        ai="스파크 감지됨",
        send_udp=True,
    )

    print("🚨 YOLO SPARK 감지 → fault_code=9 전송")


@app.route("/")
def index():
    return render_template("index.html", faults=faults, state=current_state)


@app.route("/preset/<int:code>", methods=["POST"])
def send_preset(code):
    """
    GUI 고장 버튼.
    F1~F7 버튼은 V/I를 바꾸지 않고 fault_code만 바꿈.
    """
    if code not in faults:
        return jsonify({"ok": False, "error": "Invalid preset code"}), 400

    label, desc = faults[code]

    # 전원이 OFF일 때 고장 버튼을 누르면 전원은 켜지지 않음.
    # 단, fault_code는 전송됨.
    set_state(
        code=code,
        label=label,
        desc=desc,
        ai="수동 고장 버튼 입력",
        send_udp=True,
    )

    return jsonify(make_state_response())


@app.route("/power/on", methods=["POST"])
def power_on():
    """
    전원 ON.
    기본은 12V, 1A 제한.
    JSON으로 voltage/current를 주면 그 값 사용 가능.
    예:
    {"voltage": 12, "current": 1}
    """
    data = request.get_json() or {}

    try:
        voltage = float(data.get("voltage", DEFAULT_POWER_VOLTAGE))
    except (TypeError, ValueError):
        voltage = DEFAULT_POWER_VOLTAGE

    try:
        current_limit = float(data.get("current", DEFAULT_CURRENT_LIMIT))
    except (TypeError, ValueError):
        current_limit = DEFAULT_CURRENT_LIMIT

    set_power(
        on=True,
        voltage=voltage,
        current_limit=current_limit,
        ai="전원 ON",
        send_udp=True,
    )

    return jsonify(make_state_response())


@app.route("/power/off", methods=["POST"])
def power_off():
    """
    전원 OFF.
    V=0, I_limit=0, fault_code=0 전송.
    """
    set_power(
        on=False,
        ai="전원 OFF",
        send_udp=True,
    )

    return jsonify(make_state_response())


@app.route("/reset", methods=["POST"])
def reset_state():
    """
    RESET은 전원 OFF가 아니라 고장 해제.
    전원 ON 상태면 12V 또는 현재 V를 유지하고 fault_code만 0으로 만듦.
    """
    label, desc = faults[0]

    set_state(
        code=0,
        label=label,
        desc=desc,
        ai="고장 해제",
        send_udp=True,
    )

    return jsonify(make_state_response())


@app.route("/manual", methods=["POST"])
def send_manual():
    """
    수동 설정.
    voltage/current는 전원 설정값으로 저장.
    code는 fault_code로 전송.
    """
    data = request.get_json() or {}

    try:
        voltage = float(data.get("voltage", current_state["voltage"]))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid voltage"}), 400

    try:
        current_limit = float(data.get("current", current_state["current"]))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid current limit"}), 400

    try:
        code = int(data.get("code", current_state["code"]))
    except (TypeError, ValueError):
        code = current_state["code"]

    if code not in faults:
        code = 0

    current_state["power"] = voltage > 0
    current_state["voltage"] = voltage
    current_state["current"] = current_limit

    label, desc = faults[code]

    set_state(
        code=code,
        label="MANUAL" if code == 0 else label,
        desc="직접 입력" if code == 0 else desc,
        ai="직접 입력",
        send_udp=True,
    )

    return jsonify(make_state_response())


# ================================
# 카메라 모드 ON/OFF
# ================================
@app.route("/camera_mode", methods=["GET", "POST"])
def camera_mode():
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "camera_mode": current_state["camera_mode"],
            "ai": current_state["ai"],
        })

    data = request.get_json() or {}
    enabled = bool(data.get("enabled", False))

    current_state["camera_mode"] = enabled

    if enabled:
        current_state["ai"] = "카메라 모드 ON / 감시 중"
        print("📷 카메라 모드 ON → YOLO 감지 활성화")
    else:
        current_state["ai"] = "카메라 모드 OFF"
        print("📷 카메라 모드 OFF → YOLO 감지 비활성화")

    return jsonify({
        "ok": True,
        "camera_mode": current_state["camera_mode"],
        "ai": current_state["ai"],
    })


@app.route("/camera/on", methods=["POST"])
def camera_on():
    current_state["camera_mode"] = True
    current_state["ai"] = "카메라 모드 ON / 감시 중"
    print("📷 카메라 모드 ON → YOLO 감지 활성화")

    return jsonify(make_state_response())


@app.route("/camera/off", methods=["POST"])
def camera_off():
    current_state["camera_mode"] = False
    current_state["ai"] = "카메라 모드 OFF"
    print("📷 카메라 모드 OFF → YOLO 감지 비활성화")

    return jsonify(make_state_response())


@app.route("/state")
def get_state():
    return jsonify(make_state_response())


def make_state_response():
    return {
        "ok": True,
        "power": current_state["power"],
        "code": current_state["code"],
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
        "camera_mode": current_state["camera_mode"],
    }


def generate_frames():
    """
    카메라 영상 스트리밍.
    camera_mode OFF: YOLO 추론 안 함, 원본 영상만 출력.
    camera_mode ON: YOLO 추론 후 감지 시 SPARK fault_code=9 전송.
    """
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("⚠️ 카메라를 열 수 없습니다.")
        return

    while True:
        success, frame = camera.read()

        if not success:
            break

        if current_state["camera_mode"]:
            results = model(frame, conf=0.4, verbose=False)
            annotated_frame = results[0].plot()

            if len(results[0].boxes) > 0:
                trigger_spark_by_ai()
            else:
                current_state["ai"] = "카메라 모드 ON / 감시 중"

            output_frame = annotated_frame
        else:
            current_state["ai"] = "카메라 모드 OFF"
            output_frame = frame

        ret, buffer = cv2.imencode(".jpg", output_frame)

        if not ret:
            continue

        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
        )

    camera.release()


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)