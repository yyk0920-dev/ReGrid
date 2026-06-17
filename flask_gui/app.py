import os
import sys
import socket
import struct
import threading
import time
import csv
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
from ultralytics import YOLO

from n8n_webhook import get_webhook_urls, send_daily_report_request, send_regrid_event

app = Flask(__name__)

HOST = "127.0.0.1"
PORT = 8000

# ============================================================
# ReGrid command mode
# ============================================================
# 현재 구조:
# Flask GUI 버튼 입력
# → UDP로 Simulink UDP Receive에 [voltage_cmd, fault_code_cmd] 전송
#
# Simulink UDP Receive 설정:
# Local address: 0.0.0.0
# Local port: 6008
# Remote address: 0.0.0.0
# Data size: [2 1]
# Source data type: single
# Byte order: big-endian
# ============================================================

COMMAND_MODE = "UDP"

SIMULINK_UDP_IP = os.getenv("REGRID_SIMULINK_UDP_IP", "127.0.0.1")
SIMULINK_UDP_PORT = int(os.getenv("REGRID_SIMULINK_UDP_PORT", "6008"))

# Simulink 쪽 Remote address를 0.0.0.0으로 해두면 source port는 크게 상관없음.
# 그래도 필요할 때를 대비해서 기본 송신 포트는 7008로 둠.
SIMULINK_UDP_LOCAL_PORT = int(os.getenv("REGRID_SIMULINK_UDP_LOCAL_PORT", "7008"))

DEFAULT_VOLTAGE = 22900.0
POWER_OFF_VOLTAGE = 0.0

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
    "desc": "22.9kV 메인 전원 인가",
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

# 이 표는 AI 학습/분류 기준표임.
# Simulink로 강제로 흘려보내는 전류값이 아님.
AI_REFERENCE_SCENARIOS = {
    0: {
        "case": 8,
        "system_state": "N",
        "fault_type": "N",
        "fault_details": "정상",
        "phase_current_a": 200,
        "phase_current_b": 200,
        "phase_current_c": 200,
        "fc": 8,
    },
    1: {
        "case": 1,
        "system_state": "F",
        "fault_type": "F1",
        "fault_details": "3상 단락 (A-B-C)",
        "phase_current_a": 8000,
        "phase_current_b": 8000,
        "phase_current_c": 8000,
        "fc": 1,
    },
    2: {
        "case": 2,
        "system_state": "F",
        "fault_type": "F2",
        "fault_details": "2상 단락 (A-B)",
        "phase_current_a": 6500,
        "phase_current_b": 6500,
        "phase_current_c": 200,
        "fc": 2,
    },
    3: {
        "case": 3,
        "system_state": "F",
        "fault_type": "F3",
        "fault_details": "2상 단락 (B-C)",
        "phase_current_a": 200,
        "phase_current_b": 6500,
        "phase_current_c": 6500,
        "fc": 3,
    },
    4: {
        "case": 4,
        "system_state": "F",
        "fault_type": "F4",
        "fault_details": "2상 단락 (C-A)",
        "phase_current_a": 6500,
        "phase_current_b": 200,
        "phase_current_c": 6500,
        "fc": 4,
    },
    5: {
        "case": 5,
        "system_state": "F",
        "fault_type": "F5",
        "fault_details": "1선 지락 (A-G)",
        "phase_current_a": 4500,
        "phase_current_b": 250,
        "phase_current_c": 250,
        "fc": 5,
    },
    6: {
        "case": 6,
        "system_state": "F",
        "fault_type": "F6",
        "fault_details": "1선 지락 (B-G)",
        "phase_current_a": 250,
        "phase_current_b": 4500,
        "phase_current_c": 250,
        "fc": 6,
    },
    7: {
        "case": 7,
        "system_state": "F",
        "fault_type": "F7",
        "fault_details": "1선 지락 (C-G)",
        "phase_current_a": 250,
        "phase_current_b": 250,
        "phase_current_c": 4500,
        "fc": 7,
    },
}


def get_ai_reference(code):
    code = int(code)
    return AI_REFERENCE_SCENARIOS.get(code, AI_REFERENCE_SCENARIOS[0])


def send_to_simulink(voltage, code, print_log=False):
    """
    Flask → Simulink UDP command sender.

    전송 데이터:
    [voltage_cmd, fault_code_cmd]

    voltage_cmd:
    - 22900.0 = 22.9kV 메인 전압 인가
    - 0.0 = 전원 OFF

    fault_code_cmd:
    - 0 = 정상
    - 1~7 = 전력 고장 시나리오
    - 8 = 온도 고장
    - 9 = 스파크 고장

    주의:
    AI_REFERENCE_SCENARIOS의 전류값은 Simulink로 보내지 않음.
    """
    voltage = float(voltage)
    code = int(code)

    packet = struct.pack("!2f", voltage, float(code))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    local_port = "auto"

    try:
        try:
            # Simulink UDP Receive의 Remote port를 특정 포트로 제한한 경우를 대비.
            # 이미 포트가 사용 중이면 자동 포트로 전송.
            sock.bind(("0.0.0.0", SIMULINK_UDP_LOCAL_PORT))
            local_port = SIMULINK_UDP_LOCAL_PORT
        except OSError as e:
            print(
                f"[SIMULINK UDP] local port {SIMULINK_UDP_LOCAL_PORT} bind failed: {e}. "
                f"Using auto source port.",
                flush=True,
            )

        sock.sendto(packet, (SIMULINK_UDP_IP, SIMULINK_UDP_PORT))

    finally:
        sock.close()

    print(
        f"[SIMULINK UDP SEND] voltage_cmd={voltage}, "
        f"fault_code_cmd={code}, "
        f"dst={SIMULINK_UDP_IP}:{SIMULINK_UDP_PORT}, "
        f"src_port={local_port}",
        flush=True,
    )

    if print_log:
        print(
            f"[COMMAND SENT] mode=UDP, voltage={voltage}, code={code}, label={state['label']}",
            flush=True,
        )

    return {
        "mode": "udp",
        "voltage_sent": voltage,
        "fault_code_sent": code,
        "simulink_udp_ip": SIMULINK_UDP_IP,
        "simulink_udp_port": SIMULINK_UDP_PORT,
        "udp_source_port": local_port,
    }


def make_response():
    with state_lock:
        current_code = int(state["code"])
        ai_reference = get_ai_reference(current_code)

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
            "command_mode": COMMAND_MODE,
            "simulink_udp_ip": SIMULINK_UDP_IP,
            "simulink_udp_port": SIMULINK_UDP_PORT,
            "ai_reference": ai_reference,
            "ai_reference_note": "전류값은 AI 학습/분류 기준표이며 Simulink에 강제로 주입하지 않음",
        }


def action_response(action):
    try:
        result = action()
    except Exception as e:
        with state_lock:
            state["ai"] = f"UDP 전송 오류: {e}"

        print(f"[ERROR] UDP action error: {e}", flush=True)

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

    ai_reference = get_ai_reference(code)

    return {
        "action": "preset",
        "command_label": f"{label} 입력",
        "command_desc": (
            f"Simulink UDP 전송: voltage_cmd={DEFAULT_VOLTAGE}, "
            f"fault_code_cmd={code}"
        ),
        "readback": readback,
        "ai_reference": ai_reference,
    }


def set_power_on():
    with state_lock:
        state["power"] = True
        state["voltage"] = DEFAULT_VOLTAGE
        state["code"] = 0
        state["fault_code"] = 0
        state["fault_name"] = "NORMAL"
        state["label"] = "POWER ON"
        state["desc"] = "22.9kV 메인 전원 인가"
        state["ai"] = "전원 ON"

    readback = send_to_simulink(DEFAULT_VOLTAGE, 0, print_log=True)

    return {
        "action": "power",
        "command_label": "POWER ON 입력",
        "command_desc": (
            f"Simulink UDP 전송: voltage_cmd={DEFAULT_VOLTAGE}, "
            "fault_code_cmd=0"
        ),
        "readback": readback,
        "ai_reference": get_ai_reference(0),
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
        "command_desc": (
            f"Simulink UDP 전송: voltage_cmd={POWER_OFF_VOLTAGE}, "
            "fault_code_cmd=0"
        ),
        "readback": readback,
        "ai_reference": get_ai_reference(0),
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
        "command_desc": (
            f"Simulink UDP 전송: voltage_cmd={DEFAULT_VOLTAGE}, "
            "fault_code_cmd=0"
        ),
        "readback": readback,
        "ai_reference": get_ai_reference(0),
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
        state["ai"] = "스파크 감지됨 - Simulink UDP 전송 중"

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
    return render_template(
        "index.html",
        faults=faults,
        state=make_response(),
        scenarios=AI_REFERENCE_SCENARIOS,
        default_voltage=DEFAULT_VOLTAGE,
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "server": "ReGrid Flask Gateway",
        "command_mode": COMMAND_MODE,
        "simulink_udp_ip": SIMULINK_UDP_IP,
        "simulink_udp_port": SIMULINK_UDP_PORT,
        "simulink_udp_local_port": SIMULINK_UDP_LOCAL_PORT,
        "default_voltage": DEFAULT_VOLTAGE,
        "relay_control": "disabled",
        "ess_control": "disabled",
        "camera": "enabled",
        "n8n": get_webhook_urls(),
        "note": "MATLAB Engine set_param 방식 사용 안 함. UDP로 [voltage_cmd, fault_code_cmd] 전송.",
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
            "command_desc": (
                f"Simulink UDP 전송: voltage_cmd={voltage}, "
                f"fault_code_cmd={code}"
            ),
            "readback": readback,
            "fault_node": fault_node,
            "ai_reference": get_ai_reference(code),
        }

        # 외부에서 참고용으로 보내는 값이 있으면 응답에만 포함.
        # Simulink로 직접 주입하지 않음.
        if current is not None:
            result["current"] = current

        if currents is not None:
            result["currents"] = currents

        return result

    return action_response(update_manual)


@app.route("/node_decision", methods=["POST"])
def node_decision():
    """
    RPi AI 판단 결과를 Flask가 받는 용도.

    주의:
    여기서 받은 AI 전류값은 상태/로그/n8n용 참고값으로만 사용.
    Simulink로는 voltage_cmd와 fault_code_cmd만 보냄.
    실제 차단기 제어는 RPi → Simulink breaker UDP 포트로 별도 전송하는 구조 권장.
    """
    data = request.get_json(silent=True) or {}

    try:
        code = int(data.get("fault_code", data.get("code", 0)))
    except (TypeError, ValueError):
        code = 0

    if code not in faults:
        code = 0

    try:
        voltage = float(data.get("voltage", DEFAULT_VOLTAGE))
    except (TypeError, ValueError):
        voltage = DEFAULT_VOLTAGE

    fault_node = str(data.get("fault_node", data.get("node", "rpi")))
    relay_decision = data.get("relay_decision")
    currents = data.get("currents")

    label, desc, fault_name = faults[code]

    def update_from_node():
        with state_lock:
            state["power"] = voltage > 0
            state["voltage"] = voltage
            state["code"] = code
            state["fault_code"] = code
            state["fault_name"] = fault_name
            state["label"] = label
            state["desc"] = desc
            state["ai"] = f"RPi {fault_node} AI 판단"

        readback = send_to_simulink(voltage, code, print_log=True)

        result = {
            "action": "node_decision",
            "fault_node": fault_node,
            "relay_decision": relay_decision,
            "command_label": f"{fault_node} → {label}",
            "command_desc": (
                f"RPi AI 판단 결과 수신. "
                f"Simulink UDP 전송: voltage_cmd={voltage}, fault_code_cmd={code}"
            ),
            "readback": readback,
            "ai_reference": get_ai_reference(code),
        }

        if currents is not None:
            result["currents"] = currents

        return result

    return action_response(update_from_node)


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
    print(f" COMMAND_MODE = {COMMAND_MODE}", flush=True)
    print(f" SIMULINK_UDP = {SIMULINK_UDP_IP}:{SIMULINK_UDP_PORT}", flush=True)
    print(f" UDP_SOURCE_PORT = {SIMULINK_UDP_LOCAL_PORT}", flush=True)
    print(f" DEFAULT_VOLTAGE = {DEFAULT_VOLTAGE}", flush=True)
    print(" MATLAB ENGINE = DISABLED", flush=True)
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