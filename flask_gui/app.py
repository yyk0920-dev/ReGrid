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

from n8n_webhook import get_webhook_urls, send_daily_report_request, send_regrid_event

app = Flask(__name__)

HOST = "127.0.0.1"
PORT = 8000

MODEL_PATH = r"C:\Users\20240620-LapTop\Documents\카카오톡 받은 파일\circuit4.slx"

MODEL_NAME = "circuit4"
ENGINE_NAME = "ReGridEngine"
MATLAB_START_OPTIONS = "-desktop -nosplash"

VOLTAGE_BLOCK = f"{MODEL_NAME}/voltage_cmd"
FAULT_BLOCK = f"{MODEL_NAME}/fault_code_cmd"

DEFAULT_VOLTAGE = 12.0
POWER_OFF_VOLTAGE = 0.0

eng = None
eng_lock = threading.Lock()
state_lock = threading.Lock()
camera_lock = threading.Lock()

try:
    model = YOLO(BASE_DIR / "spark.pt")
    print("[YOLO] spark.pt loaded", flush=True)
except Exception as e:
    print(f"[YOLO] spark.pt load failed: {e}", flush=True)
    print("[YOLO] fallback to yolov8n.pt", flush=True)
    model = YOLO(BASE_DIR / "yolov8n.pt")


SPARK_COOLDOWN_SEC = 3.0
last_spark_time = 0.0
spark_update_running = False

state = {
    "power": True,
    "voltage": DEFAULT_VOLTAGE,
    "code": 0,
    "fault_code": 0,
    "fault_name": "NORMAL",
    "label": "POWER ON",
    "desc": "정상 전원 인가",
    "ai": "대기 중",
    "camera_mode": False,
}

faults = {
    0: ("RESET", "고장 해제 / 정상상태", "NORMAL"),
    1: ("F1", "3상 단락", "F1_ABC_SHORT"),
    2: ("F2", "A-B 단락", "F2_AB_SHORT"),
    3: ("F3", "B-C 단락", "F3_BC_SHORT"),
    4: ("F4", "C-A 단락", "F4_CA_SHORT"),
    5: ("F5", "A상 지락", "F5_A_GROUND"),
    6: ("F6", "B상 지락", "F6_B_GROUND"),
    7: ("F7", "C상 지락", "F7_C_GROUND"),
    8: ("TEMP", "온도 높음", "F8_TEMP_HIGH"),
    9: ("SPARK", "스파크 감지", "F9_SPARK"),
}

def init_matlab():
    global eng

    if eng is not None:
        return eng

    print("[MATLAB] Connecting to MATLAB Engine...", flush=True)

    names = matlab.engine.find_matlab()
    print(f"[MATLAB] Available engines: {names}", flush=True)

    if ENGINE_NAME in names:
        try:
            eng = matlab.engine.connect_matlab(ENGINE_NAME)
            print(f"[MATLAB] Connected to existing shared MATLAB: {ENGINE_NAME}", flush=True)
        except Exception as e:
            print(f"[MATLAB] Failed to connect shared MATLAB {ENGINE_NAME}: {e}", flush=True)

    if eng is None:
        try:
            print("[MATLAB] Starting MATLAB Engine in this Python session...", flush=True)
            eng = matlab.engine.start_matlab(MATLAB_START_OPTIONS)
            print("[MATLAB] Started MATLAB Engine", flush=True)
        except Exception as e:
            raise RuntimeError(
                "MATLAB Engine 연결 실패. MATLAB과 이 터미널 권한을 같게 맞추거나 "
                "관리자 PowerShell에서 실행하세요."
            ) from e

    try:
        eng.load_system(MODEL_PATH, nargout=0)
        print(f"[MATLAB] Loaded model: {MODEL_PATH}", flush=True)
    except Exception as e:
        print(f"[MATLAB] load_system failed: {e}", flush=True)
        print("[MATLAB] 이미 모델이 열려 있다면 무시 가능할 수 있음", flush=True)

    print("[MATLAB] Engine ready", flush=True)

    return eng


def send_to_simulink(voltage, code, print_log=False):
    voltage = float(voltage)
    code = int(code)

    with eng_lock:
        engine = init_matlab()

        engine.set_param(
            VOLTAGE_BLOCK,
            "Value",
            str(voltage),
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
        f"[SIMULINK READBACK] voltage_cmd={v_read}, fault_code_cmd={c_read}",
        flush=True,
    )

    if print_log:
        print(
            f"[SIMULINK UPDATED] voltage={voltage}, code={code}, label={state['label']}",
            flush=True,
        )

    return {
        "voltage_readback": v_read,
        "fault_code_readback": c_read,
    }

def make_response():
    with state_lock:
        return {
            "ok": True,
            "power": state["power"],
            "voltage": state["voltage"],
            "code": state["code"],
            "fault_code": state["fault_code"],
            "fault_name": state["fault_name"],
            "label": state["label"],
            "desc": state["desc"],
            "ai": state["ai"],
            "camera_mode": state["camera_mode"],
        }


def action_response(action):
    try:
        result = action()
    except Exception as e:
        with state_lock:
            state["ai"] = f"MATLAB 연결 오류: {e}"

        print(f"[ERROR] MATLAB action error: {e}", flush=True)

        response = make_response()
        response.update({
            "ok": False,
            "error": str(e),
        })
        return jsonify(response), 503

    response = make_response()

    if isinstance(result, dict):
        response.update(result)

    send_regrid_event(response)

    return jsonify(response)

def set_fault(code, ai_text="고장 버튼 입력"):
    code = int(code)

    if code not in faults:
        code = 0

    label, desc, fault_name = faults[code]

    with state_lock:
        state["power"] = True
        state["voltage"] = DEFAULT_VOLTAGE
        state["code"] = code
        state["fault_code"] = code
        state["fault_name"] = fault_name
        state["label"] = label
        state["desc"] = desc
        state["ai"] = ai_text

    readback = send_to_simulink(DEFAULT_VOLTAGE, code, print_log=True)

    return {
        "action": "preset",
        "command_label": f"{label} 입력",
        "command_desc": f"Simulink voltage_cmd={DEFAULT_VOLTAGE}, fault_code_cmd={code} 전송 명령",
        "readback": readback,
    }


def set_power_on():
    with state_lock:
        state["power"] = True
        state["voltage"] = DEFAULT_VOLTAGE
        state["code"] = 0
        state["fault_code"] = 0
        state["fault_name"] = "NORMAL"
        state["label"] = "POWER ON"
        state["desc"] = "정상 전원 인가"
        state["ai"] = "전원 ON"

    readback = send_to_simulink(DEFAULT_VOLTAGE, 0, print_log=True)

    return {
        "action": "power",
        "command_label": "POWER ON 입력",
        "command_desc": f"Simulink voltage_cmd={DEFAULT_VOLTAGE}, fault_code_cmd=0 전송 명령",
        "readback": readback,
    }


def set_power_off():
    with state_lock:
        state["power"] = False
        state["voltage"] = POWER_OFF_VOLTAGE
        state["code"] = 0
        state["fault_code"] = 0
        state["fault_name"] = "NORMAL"
        state["label"] = "POWER OFF"
        state["desc"] = "전원 차단"
        state["ai"] = "전원 OFF"

    readback = send_to_simulink(POWER_OFF_VOLTAGE, 0, print_log=True)

    return {
        "action": "power",
        "command_label": "POWER OFF 입력",
        "command_desc": f"Simulink voltage_cmd={POWER_OFF_VOLTAGE}, fault_code_cmd=0 전송 명령",
        "readback": readback,
    }


def reset_fault():
    with state_lock:
        state["power"] = True
        state["voltage"] = DEFAULT_VOLTAGE
        state["code"] = 0
        state["fault_code"] = 0
        state["fault_name"] = "NORMAL"
        state["label"] = "RESET"
        state["desc"] = "고장 해제 / 정상상태"
        state["ai"] = "고장 해제"

    readback = send_to_simulink(DEFAULT_VOLTAGE, 0, print_log=True)

    return {
        "action": "reset",
        "command_label": "RESET 입력",
        "command_desc": f"Simulink voltage_cmd={DEFAULT_VOLTAGE}, fault_code_cmd=0 전송 명령",
        "readback": readback,
    }

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
        with state_lock:
            state["camera_mode"] = enabled
            state["ai"] = "카메라 ON" if enabled else "카메라 OFF"

    print(
        f"[CAMERA] mode changed | camera_mode={enabled}",
        flush=True,
    )

    response = make_response()
    response.update({
        "action": "camera",
        "command_label": "CAMERA ON" if enabled else "CAMERA OFF",
        "command_desc": (
            "YOLO 스파크 감지 활성화"
            if enabled else
            "YOLO 스파크 감지 비활성화"
        ),
    })

    return response


def trigger_spark():
    global last_spark_time, spark_update_running

    with state_lock:
        camera_enabled = state["camera_mode"]

    if not camera_enabled:
        return

    now = time.time()

    if now - last_spark_time < SPARK_COOLDOWN_SEC:
        return

    if spark_update_running:
        return

    last_spark_time = now
    spark_update_running = True

    with state_lock:
        state["ai"] = "스파크 감지됨 - Simulink 전송 중"

    def update_spark_fault():
        global spark_update_running

        try:
            set_fault(9, "스파크 감지됨")
        except Exception as e:
            with state_lock:
                state["ai"] = f"스파크 전송 오류: {e}"
            print(f"[ERROR] spark update error: {e}", flush=True)
        finally:
            spark_update_running = False

    threading.Thread(
        target=update_spark_fault,
        daemon=True,
    ).start()

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", faults=faults, state=make_response())


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "server": "ReGrid Flask Gateway",
        "model": MODEL_NAME,
        "engine": ENGINE_NAME,
        "voltage_block": VOLTAGE_BLOCK,
        "fault_block": FAULT_BLOCK,
        "relay_control": "disabled",
        "camera": "enabled",
        "n8n": get_webhook_urls(),
    })


@app.route("/state", methods=["GET"])
def get_state():
    return jsonify(make_response())


@app.route("/daily_report", methods=["POST", "GET"])
def daily_report():
    data = request.get_json(silent=True) or {}

    if request.method == "GET":
        data.setdefault("date", time.strftime("%Y-%m-%d"))

    ok, payload = send_daily_report_request(data)

    return jsonify({
        "ok": ok,
        "action": "daily_report",
        "payload": payload,
        "n8n": get_webhook_urls()["unified"],
    }), 200 if ok else 502


@app.route("/preset/<int:code>", methods=["POST", "GET"])
def preset(code):
    if code not in faults:
        return jsonify({
            "ok": False,
            "error": "invalid code",
        }), 400

    return action_response(lambda: set_fault(code, "고장 버튼 입력"))


@app.route("/reset", methods=["POST", "GET"])
def reset():
    return action_response(reset_fault)


@app.route("/power/on", methods=["POST", "GET"])
def power_on():
    return action_response(set_power_on)


@app.route("/power/off", methods=["POST", "GET"])
def power_off():
    return action_response(set_power_off)


@app.route("/manual", methods=["POST"])
def manual():
    data = request.get_json(silent=True) or {}

    try:
        voltage = float(data.get("voltage", state["voltage"]))
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "invalid voltage",
        }), 400

    try:
        code = int(data.get("code", state["code"]))
    except (TypeError, ValueError):
        code = 0

    if code not in faults:
        code = 0

    current = data.get("current", data.get("current_value"))
    currents = data.get("currents")
    fault_node = str(data.get("fault_node", data.get("node", "manual")))

    label, desc, fault_name = faults[code]

    def update_manual():
        with state_lock:
            state["voltage"] = voltage
            state["power"] = voltage > 0
            state["code"] = code
            state["fault_code"] = code
            state["fault_name"] = fault_name
            state["label"] = label
            state["desc"] = desc
            state["ai"] = "직접 입력"

        readback = send_to_simulink(voltage, code, print_log=True)

        result = {
            "action": "manual",
            "command_label": "MANUAL 입력",
            "command_desc": f"Simulink voltage_cmd={voltage}, fault_code_cmd={code} 전송 명령",
            "readback": readback,
        }

        if current is not None:
            result["current"] = current

        if currents is not None:
            result["currents"] = currents

        result["fault_node"] = fault_node

        return result

    return action_response(update_manual)

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
        print("[CAMERA] camera open failed", flush=True)
        return

    print("[CAMERA] camera opened", flush=True)

    try:
        while True:
            success, frame = camera.read()

            if not success:
                print("[CAMERA] frame read failed", flush=True)
                break

            output_frame = frame

            with state_lock:
                camera_enabled = state["camera_mode"]

            if camera_enabled:
                results = model(frame, conf=0.4, verbose=False)
                output_frame = results[0].plot()

                if len(results[0].boxes) > 0:
                    trigger_spark()
                else:
                    if not spark_update_running:
                        with state_lock:
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
        print("[CAMERA] camera released", flush=True)


@app.route("/video_feed", methods=["GET"])
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )

if __name__ == "__main__":
    print("====================================", flush=True)
    print(" ReGrid Flask Gateway starting", flush=True)
    print(f" HOST = {HOST}", flush=True)
    print(f" PORT = {PORT}", flush=True)
    print(f" MODEL_NAME = {MODEL_NAME}", flush=True)
    print(f" ENGINE_NAME = {ENGINE_NAME}", flush=True)
    print(f" MODEL_PATH = {MODEL_PATH}", flush=True)
    print(f" VOLTAGE_BLOCK = {VOLTAGE_BLOCK}", flush=True)
    print(f" FAULT_BLOCK = {FAULT_BLOCK}", flush=True)
    print(" RELAY CONTROL = DISABLED", flush=True)
    print(" ESS CONTROL = DISABLED", flush=True)
    print(" CAMERA = ENABLED", flush=True)
    print(f" N8N_WEBHOOK = {get_webhook_urls()['unified']}", flush=True)
    print("====================================", flush=True)

    app.run(
        host=HOST,
        port=PORT,
        debug=True,
        use_reloader=False,
        threaded=True,
    )
