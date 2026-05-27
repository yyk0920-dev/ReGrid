import threading
from flask import Flask, jsonify, request, render_template

# ==============================
# Flask 기본 설정
# ==============================

app = Flask(__name__)

HOST = "0.0.0.0"
PORT = 8000

CONTROL_ALLOWED_CLIENTS = {"*"}

MODEL_NAME = "circuit4"
MATLAB_ENGINE_NAME = "ReGridEngine"

FAULT_BLOCK = f"{MODEL_NAME}/fault_code_cmd"

RELAY_BLOCKS = {
    "A": f"{MODEL_NAME}/relay_A_cmd",
    "B": f"{MODEL_NAME}/relay_B_cmd",
    "C": f"{MODEL_NAME}/relay_C_cmd",
}

eng = None
eng_lock = threading.Lock()
state_lock = threading.Lock()

current_state = {
    "fault_code": 0,
    "fault_name": "NORMAL",
    "relay": {
        "A": 0,
        "B": 0,
        "C": 0,
    },
    "fault_codes": {
        "A": 0,
        "B": 0,
        "C": 0,
    },
}

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
# 권한 체크
# ==============================

def is_allowed_client():
    if "*" in CONTROL_ALLOWED_CLIENTS:
        return True
    return request.remote_addr in CONTROL_ALLOWED_CLIENTS


def require_allowed_client():
    if not is_allowed_client():
        print(
            f"[BLOCKED] remote={request.remote_addr}, path={request.path}",
            flush=True,
        )
        return False
    return True


# ==============================
# MATLAB Engine 연결
# ==============================

def init_matlab():
    global eng

    if eng is not None:
        return eng

    try:
        import matlab.engine
    except ImportError:
        raise RuntimeError(
            "matlab.engine 모듈을 import할 수 없습니다. "
            "MATLAB Engine for Python 설치를 확인하세요."
        )

    names = matlab.engine.find_matlab()
    print(f"[MATLAB] shared engines: {names}", flush=True)

    if MATLAB_ENGINE_NAME not in names:
        raise RuntimeError(
            f"공유 MATLAB Engine '{MATLAB_ENGINE_NAME}'을 찾을 수 없습니다. "
            f"MATLAB 명령창에서 먼저 실행하세요:\n"
            f"matlab.engine.shareEngine('{MATLAB_ENGINE_NAME}')"
        )

    eng = matlab.engine.connect_matlab(MATLAB_ENGINE_NAME)
    print(f"[MATLAB] connected to {MATLAB_ENGINE_NAME}", flush=True)

    return eng


# ==============================
# Simulink set_param 함수
# ==============================

def set_simulink_constant(block_path, value):
    value = float(value)

    with eng_lock:
        engine = init_matlab()
        engine.set_param(block_path, "Value", str(value), nargout=0)
        readback = engine.get_param(block_path, "Value")

    return readback


def set_fault_to_simulink(fault_code):
    fault_code = int(fault_code)

    readback = set_simulink_constant(FAULT_BLOCK, fault_code)

    with state_lock:
        current_state["fault_code"] = fault_code
        current_state["fault_name"] = FAULT_NAMES.get(fault_code, "UNKNOWN")

    print(
        f"[FAULT SET_PARAM] block={FAULT_BLOCK}, "
        f"fault_code={fault_code}, readback={readback}",
        flush=True,
    )

    return readback


def send_relay_to_simulink(node, relay_cmd):
    node = str(node).upper()

    if node not in RELAY_BLOCKS:
        raise ValueError(f"invalid node: {node}")

    relay_cmd = int(relay_cmd)

    if relay_cmd not in [0, 1]:
        raise ValueError(f"invalid relay_cmd: {relay_cmd}")

    relay_block = RELAY_BLOCKS[node]

    readback = set_simulink_constant(relay_block, relay_cmd)

    with state_lock:
        current_state["relay"][node] = relay_cmd

    print(
        f"[RELAY SET_PARAM] node={node}, "
        f"block={relay_block}, relay_cmd={relay_cmd}, readback={readback}",
        flush=True,
    )

    return readback


def reset_all():
    fault_readback = set_fault_to_simulink(0)

    relay_readback = {}

    for node in ["A", "B", "C"]:
        relay_readback[node] = send_relay_to_simulink(node, 0)
        with state_lock:
            current_state["fault_codes"][node] = 0

    with state_lock:
        current_state["fault_code"] = 0
        current_state["fault_name"] = "NORMAL"

    return {
        "fault_readback": fault_readback,
        "relay_readback": relay_readback,
    }


# ==============================
# 백그라운드 릴레이 적용 함수
# ==============================

def apply_master_relay_background(source, fault_codes, relay):
    """
    A RPi 요청에는 먼저 응답하고,
    실제 MATLAB set_param은 이 함수에서 백그라운드로 처리.
    """

    try:
        readback = {}

        for node in ["A", "B", "C"]:
            if node in fault_codes:
                with state_lock:
                    current_state["fault_codes"][node] = int(fault_codes[node])

            if node in relay:
                relay_cmd = int(relay[node])

                with state_lock:
                    old_cmd = int(current_state["relay"].get(node, -1))

                # 같은 값이면 MATLAB set_param 생략
                if old_cmd == relay_cmd:
                    readback[node] = "skipped_same_value"
                    continue

                readback[node] = send_relay_to_simulink(node, relay_cmd)

        print(
            f"[MASTER RELAY APPLIED] source={source}, "
            f"fault_codes={fault_codes}, relay={relay}, readback={readback}",
            flush=True,
        )

    except Exception as e:
        print(f"[ERROR] apply_master_relay_background: {e}", flush=True)


# ==============================
# Flask Routes
# ==============================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "server": "ReGrid Flask Gateway",
        "model": MODEL_NAME,
        "matlab_engine": MATLAB_ENGINE_NAME,
    })


@app.route("/state", methods=["GET"])
def state():
    with state_lock:
        snapshot = {
            "fault_code": current_state["fault_code"],
            "fault_name": current_state["fault_name"],
            "relay": dict(current_state["relay"]),
            "fault_codes": dict(current_state["fault_codes"]),
        }
    return jsonify(snapshot)


@app.route("/preset/<int:fault_code>", methods=["POST", "GET"])
def preset(fault_code):
    if not require_allowed_client():
        return jsonify({
            "ok": False,
            "error": "client not allowed",
            "remote": request.remote_addr,
        }), 403

    try:
        readback = set_fault_to_simulink(fault_code)

        print(
            f"[PRESET] remote={request.remote_addr}, "
            f"fault_code={fault_code}, name={FAULT_NAMES.get(fault_code, 'UNKNOWN')}",
            flush=True,
        )

        return jsonify({
            "ok": True,
            "action": "preset",
            "fault_code": fault_code,
            "fault_name": FAULT_NAMES.get(fault_code, "UNKNOWN"),
            "block": FAULT_BLOCK,
            "readback": readback,
            "state": current_state,
        })

    except Exception as e:
        print(f"[ERROR] /preset/{fault_code}: {e}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/reset", methods=["POST", "GET"])
def reset():
    if not require_allowed_client():
        return jsonify({
            "ok": False,
            "error": "client not allowed",
            "remote": request.remote_addr,
        }), 403

    try:
        readback = reset_all()

        print(
            f"[RESET] remote={request.remote_addr}",
            flush=True,
        )

        return jsonify({
            "ok": True,
            "action": "reset",
            "readback": readback,
            "state": current_state,
        })

    except Exception as e:
        print(f"[ERROR] /reset: {e}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/relay/<node>/<int:relay_cmd>", methods=["POST", "GET"])
def manual_relay(node, relay_cmd):
    if not require_allowed_client():
        return jsonify({
            "ok": False,
            "error": "client not allowed",
            "remote": request.remote_addr,
        }), 403

    try:
        readback = send_relay_to_simulink(node, relay_cmd)

        return jsonify({
            "ok": True,
            "action": "manual_relay",
            "node": node.upper(),
            "relay_cmd": relay_cmd,
            "readback": readback,
            "state": current_state,
        })

    except Exception as e:
        print(f"[ERROR] /relay/{node}/{relay_cmd}: {e}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/master_relay_decision", methods=["POST"])
def master_relay_decision():
    """
    A RPi가 최종 relay 명령을 보내는 API.

    이 함수는 요청을 받으면 즉시 응답하고,
    실제 Simulink set_param은 백그라운드에서 처리한다.
    """

    if not require_allowed_client():
        return jsonify({
            "ok": False,
            "error": "client not allowed",
            "remote": request.remote_addr,
        }), 403

    try:
        data = request.get_json(force=True)

        source = data.get("source", "UNKNOWN")
        fault_codes = data.get("fault_codes", {})
        relay = data.get("relay", {})

        if not isinstance(fault_codes, dict):
            raise ValueError("fault_codes must be dict")

        if not isinstance(relay, dict):
            raise ValueError("relay must be dict")

        with state_lock:
            for node in ["A", "B", "C"]:
                if node in fault_codes:
                    current_state["fault_codes"][node] = int(fault_codes[node])

            state_snapshot = {
                "fault_code": current_state["fault_code"],
                "fault_name": current_state["fault_name"],
                "relay": dict(current_state["relay"]),
                "fault_codes": dict(current_state["fault_codes"]),
            }

        print(
            f"[MASTER RELAY RECEIVED] remote={request.remote_addr}, "
            f"source={source}, fault_codes={fault_codes}, relay={relay}",
            flush=True,
        )

        t = threading.Thread(
            target=apply_master_relay_background,
            args=(source, fault_codes, relay),
            daemon=True,
        )
        t.start()

        return jsonify({
            "ok": True,
            "action": "master_relay_decision_received",
            "message": "relay command accepted; applying in background",
            "source": source,
            "relay_request": relay,
            "state": state_snapshot,
        })

    except Exception as e:
        print(f"[ERROR] /master_relay_decision: {e}", flush=True)
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


# ==============================
# 실행부
# ==============================

if __name__ == "__main__":
    print("====================================", flush=True)
    print(" ReGrid Flask Gateway starting", flush=True)
    print(f" HOST = {HOST}", flush=True)
    print(f" PORT = {PORT}", flush=True)
    print(f" MODEL_NAME = {MODEL_NAME}", flush=True)
    print(f" MATLAB_ENGINE_NAME = {MATLAB_ENGINE_NAME}", flush=True)
    print(f" FAULT_BLOCK = {FAULT_BLOCK}", flush=True)
    print(f" RELAY_BLOCKS = {RELAY_BLOCKS}", flush=True)
    print("====================================", flush=True)

    app.run(host=HOST, port=PORT, debug=True, use_reloader=False, threaded=True)