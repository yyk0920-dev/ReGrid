import threading
import time
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, render_template, request
import matlab.engine
from ultralytics import YOLO


app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent

# =========================
# Simulink / MATLAB 설정
# =========================
MODEL_PATH = r"C:\Users\20240620-LapTop\Documents\카카오톡 받은 파일\circuit4.slx"
MODEL_NAME = "circuit4"

VOLTAGE_BLOCK = f"{MODEL_NAME}/voltage_cmd"
FAULT_BLOCK = f"{MODEL_NAME}/fault_code_cmd"

DEFAULT_VOLTAGE = 12.0
POWER_OFF_VOLTAGE = 0.0

eng = None
eng_lock = threading.Lock()

try:
    model = YOLO(BASE_DIR / "spark.pt")
    print("spark.pt loaded")
except Exception as e:
    print(f"spark.pt load failed: {e}")
    model = YOLO(BASE_DIR / "yolov8n.pt")

SPARK_COOLDOWN_SEC = 3.0
last_spark_time = 0.0


# =========================
# 현재 상태
# =========================
state = {
    "power": True,
    "voltage": DEFAULT_VOLTAGE,
    "code": 0,
    "label": "POWER ON",
    "desc": "정상 전원 인가",
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


# =========================
# MATLAB Engine 초기화
# =========================
def init_matlab():
    global eng

    if eng is not None:
        return eng

    print("Connecting to existing MATLAB Engine...")

    names = matlab.engine.find_matlab()
    print("Available MATLAB engines:", names)

    if "ReGridEngine" not in names:
        raise RuntimeError(
            "ReGridEngine을 찾지 못했습니다. MATLAB 명령창에서 "
            "matlab.engine.shareEngine('ReGridEngine')를 먼저 실행하세요."
        )

    eng = matlab.engine.connect_matlab("ReGridEngine")
    print("Connected to existing MATLAB: ReGridEngine")

    # 이미 열려 있는 모델에 붙는 용도. 새 창 띄우지 않음.
    eng.load_system(MODEL_PATH, nargout=0)

    print("MATLAB Engine ready")
    return eng


def send_to_simulink(voltage, code, print_log=False):
    with eng_lock:
        engine = init_matlab()

        engine.set_param(
            VOLTAGE_BLOCK,
            "Value",
            str(float(voltage)),
            nargout=0,
        )

        engine.set_param(
            FAULT_BLOCK,
            "Value",
            str(float(code)),
            nargout=0,
        )

        v_read = engine.get_param(VOLTAGE_BLOCK, "Value")
        c_read = engine.get_param(FAULT_BLOCK, "Value")

    print(
        f"Simulink read-back | "
        f"voltage_cmd={v_read}, fault_code_cmd={c_read}"
    )

    if print_log:
        print(
            f"Simulink updated | "
            f"voltage={float(voltage)}, "
            f"code={float(code)}, "
            f"label={state['label']}"
        )


# =========================
# 응답 함수
# =========================
def make_response():
    return {
        "ok": True,
        "power": state["power"],
        "voltage": state["voltage"],
        "code": state["code"],
        "label": state["label"],
        "desc": state["desc"],
        "ai": state["ai"],
        "camera_mode": state["camera_mode"],
    }


# =========================
# 상태 변경 함수
# =========================
def set_fault(code, ai_text="고장 버튼 입력"):
    if code not in faults:
        code = 0

    label, desc = faults[code]

    # F1~F9 / RESET은 전원을 끄는 게 아니라
    # 12V는 유지하고 fault_code만 바꾸는 구조
    state["power"] = True
    state["voltage"] = DEFAULT_VOLTAGE
    state["code"] = int(code)
    state["label"] = label
    state["desc"] = desc
    state["ai"] = ai_text

    send_to_simulink(state["voltage"], state["code"], print_log=True)


def set_power_on():
    state["power"] = True
    state["voltage"] = DEFAULT_VOLTAGE
    state["code"] = 0
    state["label"] = "POWER ON"
    state["desc"] = "정상 전원 인가"
    state["ai"] = "전원 ON"

    send_to_simulink(state["voltage"], state["code"], print_log=True)


def set_power_off():
    state["power"] = False
    state["voltage"] = POWER_OFF_VOLTAGE
    state["code"] = 0
    state["label"] = "POWER OFF"
    state["desc"] = "전원 차단"
    state["ai"] = "전원 OFF"

    send_to_simulink(state["voltage"], state["code"], print_log=True)


def trigger_spark():
    global last_spark_time

    if not state["camera_mode"]:
        return

    now = time.time()

    if now - last_spark_time < SPARK_COOLDOWN_SEC:
        return

    last_spark_time = now
    set_fault(9, "스파크 감지됨")


# =========================
# Flask 라우트
# =========================
@app.route("/")
def index():
    return render_template("index.html", faults=faults, state=state)


@app.route("/state")
def get_state():
    return jsonify(make_response())


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
    set_power_on()
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
        code = 0

    if code not in faults:
        code = 0

    label, desc = faults[code]

    state["voltage"] = voltage
    state["power"] = voltage > 0
    state["code"] = code
    state["label"] = label
    state["desc"] = desc
    state["ai"] = "직접 입력"

    send_to_simulink(state["voltage"], state["code"], print_log=True)
    return jsonify(make_response())


# =========================
# YOLO 카메라 제어
# =========================
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


def generate_frames():
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("camera open failed")
        return

    try:
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
    finally:
        camera.release()


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# =========================
# 실행부
# =========================
if __name__ == "__main__":
    # 실행할 때 한 번 MATLAB/Simulink 연결하고 초기값 넣기
    send_to_simulink(DEFAULT_VOLTAGE, 0, print_log=True)

    app.run(
        host="127.0.0.1",
        port=8000,
        debug=True,
        use_reloader=False,
    )
