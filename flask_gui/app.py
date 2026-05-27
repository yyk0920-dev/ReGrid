import os
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"


def use_project_venv_when_run_directly():
    running_this_file = Path(sys.argv[0]).resolve() == Path(__file__).resolve()
    already_using_venv = Path(sys.executable).resolve() == VENV_PYTHON.resolve()

    if (
        running_this_file
        and VENV_PYTHON.exists()
        and not already_using_venv
        and os.environ.get("REGRID_VENV_BOOTSTRAPPED") != "1"
    ):
        os.environ["REGRID_VENV_BOOTSTRAPPED"] = "1"
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])


use_project_venv_when_run_directly()

import cv2
from flask import Flask, Response, jsonify, render_template, request
import matlab.engine
from ultralytics import YOLO


app = Flask(__name__)

# =========================
# Simulink / MATLAB 설정
# =========================
MODEL_PATH = r"C:\Users\20240620-LapTop\Documents\카카오톡 받은 파일\circuit4.slx"
MODEL_NAME = "circuit4"
ENGINE_NAME = "ReGridEngine"
MATLAB_START_OPTIONS = "-desktop -nosplash"

VOLTAGE_BLOCK = f"{MODEL_NAME}/voltage_cmd"
FAULT_BLOCK = f"{MODEL_NAME}/fault_code_cmd"
ESS_BLOCK = f"{MODEL_NAME}/ess_cmd"

DEFAULT_VOLTAGE = 12.0
POWER_OFF_VOLTAGE = 0.0

eng = None
eng_lock = threading.Lock()
camera_lock = threading.Lock()

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

    print("Connecting to MATLAB Engine...")

    names = matlab.engine.find_matlab()
    print("Available MATLAB engines:", names)

    if ENGINE_NAME in names:
        try:
            eng = matlab.engine.connect_matlab(ENGINE_NAME)
            print(f"Connected to existing MATLAB: {ENGINE_NAME}")
        except Exception as e:
            print(f"Failed to connect to shared MATLAB {ENGINE_NAME}: {e}")

    if eng is None:
        try:
            print("Starting a MATLAB Engine in this Python session...")
            eng = matlab.engine.start_matlab(MATLAB_START_OPTIONS)
            print("Started MATLAB Engine")
        except Exception as e:
            raise RuntimeError(
                "MATLAB Engine 연결에 실패했습니다. MATLAB과 이 터미널의 권한을 "
                "같게 맞추거나 관리자 PowerShell에서 실행하세요."
            ) from e

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
        f"voltage_cmd={v_read}, fault_code_cmd={c_read}",
        flush=True,
    )

    if print_log:
        print(
            f"Simulink updated | "
            f"voltage={float(voltage)}, "
            f"code={float(code)}, "
            f"label={state['label']}",
            flush=True,
        )


# =========================
# 응답 함수
# =========================
def make_response():
    command_label = f"{state['label']} 입력"
    command_desc = (
        f"Simulink voltage_cmd={state['voltage']}, "
        f"fault_code_cmd={state['code']} 전송 명령"
    )

    return {
        "ok": True,
        "power": state["power"],
        "voltage": state["voltage"],
        "code": state["code"],
        "label": state["label"],
        "desc": state["desc"],
        "command_label": command_label,
        "command_desc": command_desc,
        "ai": state["ai"],
        "camera_mode": state["camera_mode"],
    }


def action_response(action):
    try:
        action()
    except Exception as e:
        state["ai"] = f"MATLAB 연결 오류: {e}"
        print(f"MATLAB action error: {e}", flush=True)
        return jsonify({
            **make_response(),
            "ok": False,
            "error": str(e),
        }), 503

    return jsonify(make_response())


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

    with camera_lock:
        camera_enabled = state["camera_mode"]

    if not camera_enabled:
        return

    now = time.time()

    if now - last_spark_time < SPARK_COOLDOWN_SEC:
        return

    last_spark_time = now
    state["ai"] = "스파크 감지됨 - Simulink 고장코드는 변경하지 않음"
    print(
        "Spark detected by camera; Simulink fault_code_cmd unchanged.",
        flush=True,
    )


# =========================
# 카메라 상태 제어 함수
# =========================
def parse_bool(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    if isinstance(value, str):
        value = value.strip().lower()
        return value in ["1", "true", "on", "yes", "y"]

    return False


def set_camera_enabled(enabled):
    enabled = bool(enabled)

    with camera_lock:
        state["camera_mode"] = enabled
        state["ai"] = "카메라 ON" if enabled else "카메라 OFF"

    print(
        f"Camera mode changed | camera_mode={state['camera_mode']}",
        flush=True,
    )

    return make_response()


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

    return action_response(lambda: set_fault(code, "고장 버튼 입력"))


@app.route("/reset", methods=["POST"])
def reset():
    return action_response(lambda: set_fault(0, "고장 해제"))


@app.route("/power/on", methods=["POST"])
def power_on():
    return action_response(set_power_on)


@app.route("/power/off", methods=["POST"])
def power_off():
    return action_response(set_power_off)


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

    return action_response(
        lambda: send_to_simulink(state["voltage"], state["code"], print_log=True)
    )


# =========================
# YOLO 카메라 제어 라우트
# =========================
@app.route("/camera/on", methods=["POST", "GET"])
def camera_on():
    return jsonify(set_camera_enabled(True))


@app.route("/camera/off", methods=["POST", "GET"])
def camera_off():
    return jsonify(set_camera_enabled(False))


@app.route("/camera_mode", methods=["GET", "POST"])
def camera_mode():
    if request.method == "GET":
        return jsonify(make_response())

    data = request.get_json(silent=True) or {}
    enabled = parse_bool(data.get("enabled", False))

    return jsonify(set_camera_enabled(enabled))


def generate_frames():
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("camera open failed", flush=True)
        return

    try:
        while True:
            success, frame = camera.read()

            if not success:
                break

            output_frame = frame

            with camera_lock:
                camera_enabled = state["camera_mode"]

            if camera_enabled:
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


@app.post("/ess/<int:cmd>")
def set_ess(cmd):
    cmd = 1 if cmd else 0

    def update_ess():
        with eng_lock:
            engine = init_matlab()
            engine.set_param(ESS_BLOCK, "Value", str(cmd), nargout=0)
            readback = engine.get_param(ESS_BLOCK, "Value")

        print(f"ESS updated | ess_cmd={readback}", flush=True)
        return readback

    try:
        readback = update_ess()
    except Exception as e:
        state["ai"] = f"MATLAB 연결 오류: {e}"
        print(f"ESS update error: {e}", flush=True)
        return jsonify({"ok": False, "ess_cmd": cmd, "error": str(e)}), 503

    return jsonify({"ok": True, "ess_cmd": cmd, "readback": readback})


# =========================
# 실행부
# =========================
if __name__ == "__main__":
    print("Flask server starting. MATLAB connects on the first control request.")
    app.run(
        host="0.0.0.0",
        port=8000,
        debug=True,
        use_reloader=False,
    )
