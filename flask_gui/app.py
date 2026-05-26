import socket
import struct
import time
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, render_template, request
from ultralytics import YOLO

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent

VOLTAGE_UDP_IP = "127.0.0.1"
VOLTAGE_UDP_PORT = 5000
voltage_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

FAULT_UDP_IP = "127.0.0.1"
FAULT_UDP_PORT = 5001
fault_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

DEFAULT_VOLTAGE = 12.0
POWER_OFF_VOLTAGE = 0.0

try:
    model = YOLO(BASE_DIR / "spark.pt")
    print("spark.pt loaded")
except Exception as e:
    print(f"spark.pt load failed: {e}")
    model = YOLO(BASE_DIR / "yolov8n.pt")

SPARK_COOLDOWN_SEC = 3.0
last_spark_time = 0.0

state = {
    "power": True,
    "code": 0,
    "label": "POWER ON",
    "desc": "정상 전원 인가",
    "voltage": DEFAULT_VOLTAGE,
    "ai": "대기 중",
    "camera_mode": False,
}

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
    9: ("SPARK", "스파크 감지"),
}


def send_voltage_udp(voltage):
    msg = struct.pack(">f", float(voltage))
    voltage_sock.sendto(msg, (VOLTAGE_UDP_IP, VOLTAGE_UDP_PORT))


def send_fault_udp(code):
    msg = struct.pack(">f", float(int(code)))
    fault_sock.sendto(msg, (FAULT_UDP_IP, FAULT_UDP_PORT))


def send_all():
    send_voltage_udp(state["voltage"])
    send_fault_udp(state["code"])
    print(
        f"UDP sent: power={state['power']}, "
        f"voltage={state['voltage']}, "
        f"code={state['code']}, "
        f"label={state['label']}"
    )


def make_response():
    return {
        "ok": True,
        "power": state["power"],
        "code": state["code"],
        "label": state["label"],
        "desc": state["desc"],
        "voltage": state["voltage"],
        "ai": state["ai"],
        "camera_mode": state["camera_mode"],
    }


def set_fault(code, ai_text="수동 입력"):
    if code not in faults:
        code = 0

    label, desc = faults[code]

    state["code"] = int(code)
    state["label"] = label
    state["desc"] = desc
    state["ai"] = ai_text

    send_all()


def set_power_on(voltage=DEFAULT_VOLTAGE):
    state["power"] = True
    state["voltage"] = float(voltage)
    state["code"] = 0
    state["label"] = "POWER ON"
    state["desc"] = "정상 전원 인가"
    state["ai"] = "전원 ON"

    send_all()


def set_power_off():
    state["power"] = False
    state["voltage"] = POWER_OFF_VOLTAGE
    state["code"] = 0
    state["label"] = "POWER OFF"
    state["desc"] = "전원 차단"
    state["ai"] = "전원 OFF"

    send_all()


def trigger_spark():
    global last_spark_time

    if not state["camera_mode"]:
        return

    now = time.time()

    if now - last_spark_time < SPARK_COOLDOWN_SEC:
        return

    last_spark_time = now
    set_fault(9, "스파크 감지됨")


@app.route("/")
def index():
    return render_template("index.html", faults=faults, state=state)


@app.route("/preset/<int:code>", methods=["POST"])
def preset(code):
    if code not in faults:
        return jsonify({"ok": False, "error": "invalid code"}), 400

    set_fault(code, "고장 버튼 입력")
    return jsonify(make_response())


@app.route("/reset", methods=["POST"])
def reset():
    set_fault(0, "고장 해제")
    return jsonify(make_response())


@app.route("/power/on", methods=["POST"])
def power_on():
    data = request.get_json() or {}

    try:
        voltage = float(data.get("voltage", DEFAULT_VOLTAGE))
    except (TypeError, ValueError):
        voltage = DEFAULT_VOLTAGE

    set_power_on(voltage)
    return jsonify(make_response())


@app.route("/power/off", methods=["POST"])
def power_off():
    set_power_off()
    return jsonify(make_response())


@app.route("/manual", methods=["POST"])
def manual():
    data = request.get_json() or {}

    try:
        voltage = float(data.get("voltage", state["voltage"]))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid voltage"}), 400

    try:
        code = int(data.get("code", state["code"]))
    except (TypeError, ValueError):
        code = state["code"]

    if code not in faults:
        code = 0

    state["voltage"] = voltage
    state["power"] = voltage > 0

    label, desc = faults[code]

    state["code"] = code
    state["label"] = label
    state["desc"] = desc
    state["ai"] = "직접 입력"

    send_all()
    return jsonify(make_response())


@app.route("/camera/on", methods=["POST"])
def camera_on():
    state["camera_mode"] = True
    state["ai"] = "카메라 ON"
    return jsonify(make_response())


@app.route("/camera/off", methods=["POST"])
def camera_off():
    state["camera_mode"] = False
    state["ai"] = "카메라 OFF"
    return jsonify(make_response())


@app.route("/camera_mode", methods=["GET", "POST"])
def camera_mode():
    if request.method == "GET":
        return jsonify(make_response())

    data = request.get_json() or {}
    enabled = bool(data.get("enabled", False))

    state["camera_mode"] = enabled
    state["ai"] = "카메라 ON" if enabled else "카메라 OFF"

    return jsonify(make_response())


@app.route("/state")
def get_state():
    return jsonify(make_response())


def generate_frames():
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("camera open failed")
        return

    while True:
        success, frame = camera.read()

        if not success:
            break

        output_frame = frame

        if state["camera_mode"]:
            results = model(frame, conf=0.4, verbose=False)
            output_frame = results[0].plot()

            if len(results[0].boxes) > 0:
                trigger_spark()
            else:
                state["ai"] = "카메라 ON"

        ret, buffer = cv2.imencode(".jpg", output_frame)

        if not ret:
            continue

        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame_bytes
            + b"\r\n"
        )

    camera.release()


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


if __name__ == "__main__":
    send_all()
    app.run(host="127.0.0.1", port=8000, debug=True)