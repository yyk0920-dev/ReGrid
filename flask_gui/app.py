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
# UDP 설정: Flask → Simulink
# ================================
UDP_IP = "127.0.0.1"
UDP_PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

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
    "label": "RESET",
    "desc": "정상상태",
    "voltage": 12.0,
    "current": 1.0,
    "ai": "대기 중",
}

# code: (label, desc, voltage, current)
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


def send_vi_udp(voltage, current):
    """
    voltage, current를 float 2개로 UDP 전송.
    Simulink UDP Receive:
    - Data type: single
    - Data size: [1 2]

    현재 Simulink에서 정상 표시되던 방식 유지:
    big-endian float32 2개
    """
    msg = struct.pack(">ff", float(voltage), float(current))
    sock.sendto(msg, (UDP_IP, UDP_PORT))


def set_state(label, desc, voltage, current, ai=None, send_udp=True):
    """
    웹 상태 업데이트 + 필요 시 Simulink로 V/I 전송
    """
    current_state["label"] = label
    current_state["desc"] = desc
    current_state["voltage"] = float(voltage)
    current_state["current"] = float(current)

    if ai is not None:
        current_state["ai"] = ai

    if send_udp:
        send_vi_udp(voltage, current)
        print(f"Sent UDP: {label} / V={voltage}, I={current}")


def trigger_spark_by_ai():
    """
    YOLO가 스파크를 감지했을 때 자동으로 SPARK 상태 적용.
    너무 자주 전송되지 않도록 쿨타임 적용.
    """
    global last_spark_trigger_time

    now = time.time()

    if now - last_spark_trigger_time < SPARK_COOLDOWN_SEC:
        return

    last_spark_trigger_time = now

    label, desc, voltage, current = faults[9]

    set_state(
        label=label,
        desc=desc,
        voltage=voltage,
        current=current,
        ai="스파크 감지됨",
        send_udp=True
    )

    print("🚨 YOLO SPARK 감지 → SPARK V/I 값을 Simulink로 전송")


@app.route("/")
def index():
    return render_template("index.html", faults=faults, state=current_state)


@app.route("/preset/<int:code>", methods=["POST"])
def send_preset(code):
    if code not in faults:
        return jsonify({"ok": False, "error": "Invalid preset code"}), 400

    label, desc, voltage, current = faults[code]

    set_state(
        label=label,
        desc=desc,
        voltage=voltage,
        current=current,
        ai="수동 버튼 입력",
        send_udp=True
    )

    return jsonify({
        "ok": True,
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
    })


@app.route("/manual", methods=["POST"])
def send_manual():
    data = request.get_json()

    try:
        voltage = float(data.get("voltage"))
        current = float(data.get("current"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid voltage/current"}), 400

    set_state(
        label="MANUAL",
        desc="직접 입력",
        voltage=voltage,
        current=current,
        ai="직접 입력",
        send_udp=True
    )

    return jsonify({
        "ok": True,
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
    })


@app.route("/state")
def get_state():
    """
    프론트엔드에서 YOLO 감지 후 상태 자동 반영하려고 주기적으로 읽는 API
    """
    return jsonify({
        "ok": True,
        "label": current_state["label"],
        "desc": current_state["desc"],
        "voltage": current_state["voltage"],
        "current": current_state["current"],
        "ai": current_state["ai"],
    })


def generate_frames():
    """
    YOLO 실시간 영상 스트리밍.
    카메라 0번을 사용함.
    """
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("⚠️ 카메라를 열 수 없습니다.")
        return

    while True:
        success, frame = camera.read()

        if not success:
            break

        results = model(frame, conf=0.4, verbose=False)

        annotated_frame = results[0].plot()

        # 스파크 또는 학습 클래스가 검출되면 SPARK 상태 자동 전송
        if len(results[0].boxes) > 0:
            trigger_spark_by_ai()
        else:
            current_state["ai"] = "감시 중"

        ret, buffer = cv2.imencode(".jpg", annotated_frame)

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
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    # PC에서만 접속할 거면 127.0.0.1
    # 다른 기기에서도 웹 접속하려면 0.0.0.0
    app.run(host="127.0.0.1", port=8000, debug=True)
