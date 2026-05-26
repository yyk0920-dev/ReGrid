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
# UDP 설정 1: Flask → Simulink V 전송
# ================================
# 이제 전압 V 하나만 보냄
# Simulink UDP Receive 설정:
# - Local port: 5000
# - Data type: single
# - Data size: [1]
# - Byte order: Big Endian
V_UDP_IP = "127.0.0.1"
V_UDP_PORT = 5000
v_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ================================
# UDP 설정 2: Flask → Simulink Fault Code 전송
# ================================
# 버튼을 눌렀을 때 Simulink 안의 단락/지락 스위치 제어용
# Simulink UDP Receive 설정:
# - Local port: 5001
# - Data type: single
# - Data size: [1]
# - Byte order: Big Endian
FAULT_UDP_IP = "127.0.0.1"
FAULT_UDP_PORT = 5001
fault_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

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

# 스파크 감지 시 UDP를 너무 많이 보내지 않도록 쿨타임 적용
SPARK_COOLDOWN_SEC = 3.0
last_spark_trigger_time = 0.0

# ================================
# 현재 상태
# ================================
current_state = {
    "code": 0,
    "label": "RESET",
    "desc": "정상상태",
    "voltage": 12.0,

    # UI 호환용으로 current는 남겨둠.
    # 단, Simulink로는 더 이상 current를 보내지 않음.
    "current": 1.0,

    "ai": "대기 중",
    "camera_mode": False,
}

# code: (label, desc, voltage, current)
# current는 화면 표시/기존 UI 호환용. 실제 Simulink 입력에는 사용하지 않음.
faults = {
    0: ("RESET", "정상상태", 12.0, 1.0),
    1: ("F1", "3상 단락", 6.0, 8.0),
    2: ("F2", "A-B 단락", 8.0, 6.5),
    3: ("F3", "B-C 단락", 8.5, 6.0),
    4: ("F4", "C-A 단락", 8.0, 6.0),
    5: ("F5", "A상 지락", 9.0, 4.5),
    6: ("F6", "B상 지락", 9.2, 4.2),
    7: ("F7", "C상 지락", 9.5, 4.0),
    8: ("TEMP", "온도 높음", 12.0, 3.0),
    9: ("SPARK", "스파크 감지 / 화재+소리", 5.0, 7.0),
}


def send_voltage_udp(voltage):
    """
    voltage 하나만 float32 single로 UDP 전송.

    Simulink UDP Receive 설정:
    - Local port: 5000
    - Data type: single
    - Data size: [1]
    - Byte order: Big Endian
    """
    msg = struct.pack(">f", float(voltage))
    v_sock.sendto(msg, (V_UDP_IP, V_UDP_PORT))


def send_fault_code_udp(code):
    """
    fault code를 float32 single로 UDP 전송.

    Simulink UDP Receive 설정:
    - Local port: 5001
    - Data type: single
    - Data size: [1]
    - Byte order: Big Endian

    code 의미:
    0 = RESET / 정상
    1 = 3상 단락
    2 = A-B 단락
    3 = B-C 단락
    4 = C-A 단락
    5 = A상 지락
    6 = B상 지락
    7 = C상 지락
    8 = 온도 이상
    9 = 스파크 / 소리 이상
    """
    code = int(code)

    if code < 0 or code > 255:
        raise ValueError(f"fault code must be 0~255, got {code}")

    msg = struct.pack(">f", float(code))
    fault_sock.sendto(msg, (FAULT_UDP_IP, FAULT_UDP_PORT))


def set_state(code, label, desc, voltage, current=0.0, ai=None, send_udp=True):
    """
    웹 상태 업데이트 + 필요 시 Simulink로 V와 fault code 전송.
    current는 상태표시용으로만 유지.
    """
    current_state["code"] = int(code)
    current_state["label"] = label
    current_state["desc"] = desc
    current_state["voltage"] = float(voltage)
    current_state["current"] = float(current)

    if ai is not None:
        current_state["ai"] = ai

    if send_udp:
        # 1) V 하나만 Simulink로 전송
        send_voltage_udp(voltage)

        # 2) fault code 전송
        send_fault_code_udp(code)

        print(
            f"Sent UDP: code={code}, {label} / "
            f"V={voltage}"
        )


def trigger_spark_by_ai():
    """
    YOLO가 스파크를 감지했을 때 자동으로 SPARK 상태 적용.
    camera_mode가 켜져 있을 때만 동작함.
    """
    global last_spark_trigger_time

    if not current_state["camera_mode"]:
        return

    now = time.time()

    if now - last_spark_trigger_time < SPARK_COOLDOWN_SEC:
        return

    last_spark_trigger_time = now

    code = 9
    label, desc, voltage, current = faults[code]

    set_state(
        code=code,
        label=label,
        desc=desc,
        voltage=voltage,
        current=current,
        ai="스파크 감지됨",
        send_udp=True,
    )

    print("🚨 YOLO SPARK 감지 → SPARK fault code와 V 값을 Simulink로 전송")


@app.route("/")
def index():
    return render_template("index.html", faults=faults, state=current_state)


@app.route("/preset/<int:code>", methods=["POST"])
def send_preset(code):
    if code not in faults:
        return jsonify({"ok": False, "error": "Invalid preset code"}), 400

    label, desc, voltage, current = faults[code]

    set_state(
        code=code,
        label=label,
        desc=desc,
        voltage=voltage,
        current=current,
        ai="수동 버튼 입력",
        send_udp=True,
    )

    return jsonify({
        "ok": True,
        "code": current_state["code"],
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
        "camera_mode": current_state["camera_mode"],
    })


@app.route("/manual", methods=["POST"])
def send_manual():
    data = request.get_json() or {}

    try:
        voltage = float(data.get("voltage"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid voltage"}), 400

    # current는 더 이상 Simulink로 보내지 않지만, 기존 UI 호환용으로 받으면 저장만 함
    try:
        current = float(data.get("current", 0.0))
    except (TypeError, ValueError):
        current = 0.0

    try:
        code = int(data.get("code", 0))
    except (TypeError, ValueError):
        code = 0

    if code not in faults:
        code = 0

    set_state(
        code=code,
        label="MANUAL",
        desc="직접 입력",
        voltage=voltage,
        current=current,
        ai="직접 입력",
        send_udp=True,
    )

    return jsonify({
        "ok": True,
        "code": current_state["code"],
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
        "camera_mode": current_state["camera_mode"],
    })


@app.route("/reset", methods=["POST"])
def reset_state():
    code = 0
    label, desc, voltage, current = faults[code]

    set_state(
        code=code,
        label=label,
        desc=desc,
        voltage=voltage,
        current=current,
        ai="RESET",
        send_udp=True,
    )

    return jsonify({
        "ok": True,
        "code": current_state["code"],
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
        "camera_mode": current_state["camera_mode"],
    })


# ================================
# 카메라 모드 ON/OFF
# ================================
@app.route("/camera_mode", methods=["GET", "POST"])
def camera_mode():
    """
    GET:
      현재 카메라 모드 상태 반환

    POST JSON 예:
      {"enabled": true}
      {"enabled": false}

    camera_mode가 true일 때만 YOLO 감지 결과가 SPARK fault를 발생시킴.
    """
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

    return jsonify({
        "ok": True,
        "camera_mode": current_state["camera_mode"],
        "ai": current_state["ai"],
    })


@app.route("/camera/off", methods=["POST"])
def camera_off():
    current_state["camera_mode"] = False
    current_state["ai"] = "카메라 모드 OFF"
    print("📷 카메라 모드 OFF → YOLO 감지 비활성화")

    return jsonify({
        "ok": True,
        "camera_mode": current_state["camera_mode"],
        "ai": current_state["ai"],
    })


@app.route("/state")
def get_state():
    return jsonify({
        "ok": True,
        "code": current_state["code"],
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
        "camera_mode": current_state["camera_mode"],
    })


def generate_frames():
    """
    카메라 영상 스트리밍.
    camera_mode가 OFF면 YOLO 추론을 하지 않고 원본 영상만 출력.
    camera_mode가 ON이면 YOLO 추론 후 스파크 감지 시 fault code 9 전송.
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
            # 카메라 모드 OFF일 때는 YOLO 실행 안 함
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