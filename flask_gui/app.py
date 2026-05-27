import os
import sys
import threading
import time
import socket
import struct
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

# 기존 버튼/수동 제어는 기본적으로 로컬에서만 허용
CONTROL_ALLOWED_CLIENTS = {
    item.strip()
    for item in os.environ.get("REGRID_CONTROL_ALLOWED_CLIENTS", "127.0.0.1,::1").split(",")
    if item.strip()
}

# =========================
# RPi 판단값 → Simulink 릴레이 UDP 제어 설정
# =========================
# Flask와 Simulink가 같은 노트북에서 실행되므로 127.0.0.1 사용
# 만약 Simulink UDP Receive의 Remote address를 192.168.137.1로 잡았으면
# 여기를 "192.168.137.1"로 바꿔도 됨.
SIMULINK_RELAY_IP = "127.0.0.1"

# Simulink 안의 각 노드 릴레이 UDP Receive Local Port
NODE_TO_SIM_PORT = {
    "A": 6006,
    "B": 6002,
    "C": 6003,
}

# Simulink UDP Receive의 Remote Port와 맞출 Flask 송신 포트
# Simulink 쪽 Remote port도 아래 값과 맞춰야 함.
NODE_TO_SOURCE_PORT = {
    "A": 5006,
    "B": 5002,
    "C": 5003,
}

# 각 노드가 보내준 판단값 저장
# fault_code: 0 정상, 1~9 고장
# relay_decision: 0 연결 유지, 1 차단 필요
node_decisions = {
    "A": {
        "fault_code": 0,
        "relay_decision": 0,
        "final_code": 0,
        "source": "init",
        "updated_at": None,
    },
    "B": {
        "fault_code": 0,
        "relay_decision": 0,
        "final_code": 0,
        "source": "init",
        "updated_at": None,
    },
    "C": {
        "fault_code": 0,
        "relay_decision": 0,
        "final_code": 0,
        "source": "init",
        "updated_at": None,
    },
}

eng = None
eng_lock = threading.Lock()
camera_lock = threading.Lock()
relay_lock = threading.Lock()

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


def send_to_simulink(voltage, code, print_log=False, label=None):
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
        log_label = label if label is not None else state["label"]
        print(
            f"Simulink updated | "
            f"voltage={float(voltage)}, "
            f"code={float(code)}, "
            f"label={log_label}",
            flush=True,
        )


def send_relay_to_simulink(node, final_code):
    """
    Flask가 Simulink 안의 각 노드 릴레이 UDP Receive로 최종 차단/복구 명령을 보냄.

    Simulink relay_logic 기준:
    final_code = 0   → relay_logic 출력 1 → 스위치 닫힘 / 노드 연결
    final_code = 1~9 → relay_logic 출력 0 → 스위치 열림 / 노드 차단
    """
    node = str(node).upper()

    if node not in NODE_TO_SIM_PORT:
        raise ValueError(f"invalid node: {node}")

    sim_port = NODE_TO_SIM_PORT[node]
    source_port = NODE_TO_SOURCE_PORT[node]
    final_code = int(final_code)

    data = struct.pack("<f", float(final_code))

    with relay_lock:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            # Windows에서 반복 실행 시 포트 재사용 문제 완화
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Simulink UDP Receive의 Remote port와 맞추기 위해 송신 포트 고정
            sock.bind(("0.0.0.0", source_port))
            sock.sendto(data, (SIMULINK_RELAY_IP, sim_port))

            print(
                f"[RELAY->SIMULINK] node={node}, final_code={final_code}, "
                f"src_port={source_port}, dst={SIMULINK_RELAY_IP}:{sim_port}",
                flush=True,
            )

        finally:
            sock.close()


def decide_final_code(fault_code, relay_decision):
    """
    최종 판단 로직.
    지금은 단순 버전:
    - relay_decision == 1 이거나 fault_code가 1~9면 차단
    - 둘 다 아니면 복구
    """
    fault_code = int(fault_code)
    relay_decision = int(relay_decision)

    if relay_decision == 1 or (1 <= fault_code <= 9):
        if 1 <= fault_code <= 9:
            return fault_code
        return 1

    return 0


def is_control_client_allowed():
    return "*" in CONTROL_ALLOWED_CLIENTS or request.remote_addr in CONTROL_ALLOWED_CLIENTS


def reject_remote_control(action):
    print(
        f"Blocked control request | "
        f"action={action}, remote={request.remote_addr}, path={request.path}",
        flush=True,
    )
    return jsonify({
        "ok": False,
        "error": "control endpoints are local-only",
        "remote": request.remote_addr,
    }), 403


def log_control_request(action):
    print(
        f"Control request | "
        f"action={action}, remote={request.remote_addr}, path={request.path}",
        flush=True,
    )


def require_local_control(action):
    if not is_control_client_allowed():
        return reject_remote_control(action)

    log_control_request(action)
    return None


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
        "node_decisions": node_decisions,
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

    send_to_simulink(state["voltage"], state["code"], print_log=True, label=label)


def set_power_on():
    state["power"] = True
    state["voltage"] = DEFAULT_VOLTAGE
    state["code"] = 0
    state["label"] = "POWER ON"
    state["desc"] = "정상 전원 인가"
    state["ai"] = "전원 ON"

    send_to_simulink(
        state["voltage"],
        state["code"],
        print_log=True,
        label=state["label"],
    )


def set_power_off():
    state["power"] = False
    state["voltage"] = POWER_OFF_VOLTAGE
    state["code"] = 0
    state["label"] = "POWER OFF"
    state["desc"] = "전원 차단"
    state["ai"] = "전원 OFF"

    send_to_simulink(
        state["voltage"],
        state["code"],
        print_log=True,
        label=state["label"],
    )


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
    blocked = require_local_control(f"preset:{code}")
    if blocked:
        return blocked

    if code not in faults:
        return jsonify({"ok": False, "error": "invalid code"}), 400

    return action_response(lambda: set_fault(code, "고장 버튼 입력"))


@app.route("/reset", methods=["POST"])
def reset():
    blocked = require_local_control("reset")
    if blocked:
        return blocked

    return action_response(lambda: set_fault(0, "고장 해제"))


@app.route("/power/on", methods=["POST"])
def power_on():
    blocked = require_local_control("power:on")
    if blocked:
        return blocked

    return action_response(set_power_on)


@app.route("/power/off", methods=["POST"])
def power_off():
    blocked = require_local_control("power:off")
    if blocked:
        return blocked

    return action_response(set_power_off)


@app.route("/manual", methods=["POST"])
def manual():
    blocked = require_local_control("manual")
    if blocked:
        return blocked

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
        lambda: send_to_simulink(
            state["voltage"],
            state["code"],
            print_log=True,
            label=label,
        )
    )


# =========================
# 팀원 RPi 판단값 수신 API
# =========================
@app.route("/node_decision", methods=["POST"])
def node_decision():
    """
    팀원 RPi가 자기 노드의 고장 예측/릴레이 판단 결과를 Flask로 보내는 경로.

    요청 JSON 예시:
    {
        "node": "B",
        "fault_code": 2,
        "relay_decision": 1
    }

    의미:
    node = A/B/C
    fault_code = 0 정상, 1~9 고장
    relay_decision = 0 연결 유지, 1 차단 필요
    """
    data = request.get_json(silent=True) or {}

    node = str(data.get("node", "")).strip().upper()

    try:
        fault_code = int(float(data.get("fault_code", 0)))
    except (TypeError, ValueError):
        fault_code = 0

    try:
        relay_decision = int(float(data.get("relay_decision", 0)))
    except (TypeError, ValueError):
        relay_decision = 0

    if node not in node_decisions:
        return jsonify({
            "ok": False,
            "error": "invalid node",
            "allowed_nodes": list(node_decisions.keys()),
        }), 400

    if fault_code < 0:
        fault_code = 0

    if fault_code > 9:
        fault_code = 9

    relay_decision = 1 if relay_decision else 0

    final_code = decide_final_code(fault_code, relay_decision)

    node_decisions[node] = {
        "fault_code": fault_code,
        "relay_decision": relay_decision,
        "final_code": final_code,
        "source": request.remote_addr,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    print(
        f"[NODE DECISION] remote={request.remote_addr}, "
        f"node={node}, fault_code={fault_code}, "
        f"relay_decision={relay_decision}, final_code={final_code}",
        flush=True,
    )

    try:
        send_relay_to_simulink(node, final_code)
    except Exception as e:
        state["ai"] = f"릴레이 UDP 전송 오류: {e}"
        print(f"[ERROR] relay send failed: {e}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(e),
            "node": node,
            "fault_code": fault_code,
            "relay_decision": relay_decision,
            "final_code": final_code,
            "node_decisions": node_decisions,
        }), 503

    state["ai"] = (
        f"노드 {node} 판단 수신: "
        f"fault_code={fault_code}, relay={relay_decision}, final={final_code}"
    )

    return jsonify({
        "ok": True,
        "node": node,
        "fault_code": fault_code,
        "relay_decision": relay_decision,
        "final_code": final_code,
        "node_decisions": node_decisions,
    })


@app.route("/node_decisions", methods=["GET"])
def get_node_decisions():
    return jsonify({
        "ok": True,
        "node_decisions": node_decisions,
    })


@app.route("/node_decision/reset", methods=["POST"])
def reset_node_decisions():
    """
    전체 노드 판단값 초기화 + Simulink 릴레이 전체 복구.
    팀원 RPi 테스트 후 한 번에 복구할 때 사용.
    """
    for node in node_decisions:
        node_decisions[node] = {
            "fault_code": 0,
            "relay_decision": 0,
            "final_code": 0,
            "source": request.remote_addr,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    errors = {}

    for node in ["A", "B", "C"]:
        try:
            send_relay_to_simulink(node, 0)
        except Exception as e:
            errors[node] = str(e)

    if errors:
        return jsonify({
            "ok": False,
            "error": "some relay reset commands failed",
            "errors": errors,
            "node_decisions": node_decisions,
        }), 503

    state["ai"] = "전체 노드 릴레이 판단 초기화"

    return jsonify({
        "ok": True,
        "node_decisions": node_decisions,
    })


# =========================
# YOLO 카메라 제어 라우트
# =========================
@app.route("/camera/on", methods=["POST", "GET"])
def camera_on():
    if request.method == "POST":
        blocked = require_local_control("camera:on")
        if blocked:
            return blocked

    return jsonify(set_camera_enabled(True))


@app.route("/camera/off", methods=["POST", "GET"])
def camera_off():
    if request.method == "POST":
        blocked = require_local_control("camera:off")
        if blocked:
            return blocked

    return jsonify(set_camera_enabled(False))


@app.route("/camera_mode", methods=["GET", "POST"])
def camera_mode():
    if request.method == "GET":
        return jsonify(make_response())

    blocked = require_local_control("camera_mode")
    if blocked:
        return blocked

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
    blocked = require_local_control(f"ess:{cmd}")
    if blocked:
        return blocked

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
    print("RPi node decision API:")
    print("  POST http://<192.168.137.1>:8000/node_decision")
    print("  GET  http://<192.168.137.1>:8000/node_decisions")
    app.run(
        host="0.0.0.0",
        port=8000,
        debug=True,
        use_reloader=False,
    )