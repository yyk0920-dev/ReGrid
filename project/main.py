import os
import socket
import struct
import time
import json
import threading
import math

import joblib
import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ================================
# 환경변수 읽기
# ================================
def get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def get_int_env(name, default):
    return int(get_env(name, str(default)))


def get_float_env(name, default):
    return float(get_env(name, str(default)))


def get_path_env(name, default):
    value = get_env(name, default)
    if os.path.isabs(value):
        return value
    return os.path.join(BASE_DIR, value)


# ================================
# 기본 설정
# ================================
NODE_ID = get_env("REGRID_NODE_ID", "node-a")

# 이 RPi가 Simulink 또는 이전 노드에서 데이터를 받는 포트
HOST = get_env("REGRID_HOST", "0.0.0.0")
PORT = get_int_env("REGRID_PORT", 5000)

# 다음 RPi로 원본 센서값 전달할 때 사용
# A -> B -> C 체인 구조
NEXT_NODE_IP = get_env("REGRID_CHAIN_NEXT_NODE", None)
NEXT_NODE_PORT = get_int_env("REGRID_CHAIN_NEXT_PORT", 5000)

# A 노드 IP
# B/C가 자기 예측 결과를 A로 보낼 때 사용
MASTER_NODE_IP = get_env("REGRID_MASTER_IP", None)
STATUS_PORT = get_int_env("REGRID_STATUS_PORT", 7001)

# Simulink가 돌아가는 노트북 IP
# A 노드만 필요
SIMULINK_LAPTOP_IP = get_env("REGRID_SIMULINK_IP", None)

# Simulink에서 ESS 명령을 받는 UDP Receive 포트
SIMULINK_ESS_PORT = get_int_env("REGRID_SIMULINK_ESS_PORT", 6010)

# Simulink에서 Node C 분리 스위치 명령을 받는 UDP Receive 포트
# 너희 회로 기준: Node C UDP Receive3 Port 6003
SIMULINK_ISOLATE_PORT = get_int_env("REGRID_SIMULINK_ISOLATE_PORT", 6003)

BYTE_ORDER = get_env("REGRID_BYTE_ORDER", "big")
DATA_MODE = get_env("REGRID_DATA_MODE", "auto")

MODEL_PATH = get_path_env("REGRID_MODEL_PATH", "models/regrid_fault_model.pkl")

# A 노드 여부
IS_MASTER = get_int_env(
    "REGRID_IS_MASTER",
    1 if NODE_ID == "node-a" else 0
)

ENABLE_SIMULINK_CONTROL = get_int_env("REGRID_ENABLE_SIMULINK_CONTROL", 1)
DEBUG = get_int_env("REGRID_DEBUG", 0)

# 1이면 ESS/분리 명령이 바뀔 때만 전송
# 0이면 매번 계속 전송
SEND_ON_CHANGE_ONLY = get_int_env("REGRID_SEND_ON_CHANGE_ONLY", 0)

# AI 예측 신뢰도 기준
CONF_THRESHOLD = get_float_env("REGRID_CONF_THRESHOLD", 0.80)

# B/C에서 온 메시지가 이 시간보다 오래되면 판단에서 제외
STALE_SEC = get_float_env("REGRID_STALE_SEC", 2.0)

# 상태 출력 주기
PRINT_INTERVAL = get_float_env("REGRID_PRINT_INTERVAL", 0.5)

# UDP 명령 반복 전송 횟수
COMMAND_REPEAT = get_int_env("REGRID_COMMAND_REPEAT", 5)


# ================================
# 전역 상태
# ================================
last_ess_cmd = None
last_print_time = 0.0

status_lock = threading.Lock()

latest_status = {
    "node-a": {
        "fault_code": 0,
        "fault_name": "Normal",
        "confidence": 0.0,
        "timestamp": 0.0,
    },
    "node-b": {
        "fault_code": 0,
        "fault_name": "Normal",
        "confidence": 0.0,
        "timestamp": 0.0,
    },
    "node-c": {
        "fault_code": 0,
        "fault_name": "Normal",
        "confidence": 0.0,
        "timestamp": 0.0,
    },
}


# ================================
# 모델 로드
# ================================
def load_ai_model():
    print(f"[MODEL] loading: {MODEL_PATH}")

    saved = joblib.load(MODEL_PATH)

    model = saved["model"]
    feature_cols = saved["feature_cols"]
    label_names = saved["label_names"]

    print("[MODEL] loaded successfully")
    print(f"[MODEL] feature count: {len(feature_cols)}")
    print(f"[MODEL] labels: {label_names}")

    return model, feature_cols, label_names


# ================================
# UDP 데이터 해석
# ================================
def unpack_float_array(data):
    count = len(data) // 4
    prefix = ">" if BYTE_ORDER == "big" else "<"
    fmt = prefix + ("f" * count)
    return struct.unpack(fmt, data[:count * 4])


def unpack_double_array(data):
    count = len(data) // 8
    prefix = ">" if BYTE_ORDER == "big" else "<"
    fmt = prefix + ("d" * count)
    return struct.unpack(fmt, data[:count * 8])


def decode_udp_values(data):
    """
    Simulink에서 오는 값 해석.

    권장 Simulink 설정:
    [Ia, Ib, Ic, Temperature, Sound]
    Data Type Conversion: single
    Mux input count: 5
    UDP Send → RPi: 20 bytes

    len=20이면 single 5개
    len=40이면 double 5개
    """
    if len(data) < 4:
        raise ValueError(f"packet too short: len={len(data)}")

    if DATA_MODE == "float":
        return unpack_float_array(data)

    if DATA_MODE == "double":
        return unpack_double_array(data)

    if len(data) == 20:
        return unpack_float_array(data)

    if len(data) == 40:
        return unpack_double_array(data)

    if len(data) % 4 == 0:
        return unpack_float_array(data)

    if len(data) % 8 == 0:
        return unpack_double_array(data)

    raise ValueError(f"unsupported packet length: {len(data)}")


# ================================
# AI Feature 생성
# values = [Ia, Ib, Ic, temperature, sound]
# ================================
def make_features(values):
    if len(values) < 5:
        raise ValueError(
            f"need 5 values: [Ia, Ib, Ic, temperature, sound], got {len(values)}"
        )

    Ia = float(values[0])
    Ib = float(values[1])
    Ic = float(values[2])
    temperature = float(values[3])
    sound = float(values[4])

    raw_values = [Ia, Ib, Ic, temperature, sound]

    if not all(math.isfinite(v) for v in raw_values):
        raise ValueError("NaN or inf detected in input values")

    eps = 1e-6

    Ia_abs = abs(Ia)
    Ib_abs = abs(Ib)
    Ic_abs = abs(Ic)

    I_sum = Ia_abs + Ib_abs + Ic_abs
    I_mean = (Ia_abs + Ib_abs + Ic_abs) / 3
    I_max = max(Ia_abs, Ib_abs, Ic_abs)
    I_min = min(Ia_abs, Ib_abs, Ic_abs)
    I_range = I_max - I_min
    I_std = pd.Series([Ia_abs, Ib_abs, Ic_abs]).std()

    Ia_ratio = Ia_abs / (I_sum + eps)
    Ib_ratio = Ib_abs / (I_sum + eps)
    Ic_ratio = Ic_abs / (I_sum + eps)

    Iab_diff = abs(Ia_abs - Ib_abs)
    Ibc_diff = abs(Ib_abs - Ic_abs)
    Ica_diff = abs(Ic_abs - Ia_abs)

    imbalance = I_range / (I_mean + eps)

    return {
        "Ia": Ia,
        "Ib": Ib,
        "Ic": Ic,
        "Ia_abs": Ia_abs,
        "Ib_abs": Ib_abs,
        "Ic_abs": Ic_abs,
        "I_sum": I_sum,
        "I_mean": I_mean,
        "I_max": I_max,
        "I_min": I_min,
        "I_range": I_range,
        "I_std": I_std,
        "Ia_ratio": Ia_ratio,
        "Ib_ratio": Ib_ratio,
        "Ic_ratio": Ic_ratio,
        "Iab_diff": Iab_diff,
        "Ibc_diff": Ibc_diff,
        "Ica_diff": Ica_diff,
        "imbalance": imbalance,
        "temperature": temperature,
        "sound": sound,
    }


# ================================
# AI 고장 예측
# ================================
def predict_fault(values, model, feature_cols, label_names):
    features = make_features(values)
    X = pd.DataFrame([features])[feature_cols]

    pred_code = int(model.predict(X)[0])

    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)[0]
        confidence = float(max(proba))
    else:
        confidence = 1.0

    pred_name = label_names.get(pred_code, "UNKNOWN")

    return {
        "fault_code": pred_code,
        "fault_name": pred_name,
        "confidence": confidence,
        "ia": float(values[0]),
        "ib": float(values[1]),
        "ic": float(values[2]),
        "temperature": float(values[3]),
        "sound": float(values[4]),
    }


# ================================
# A 노드로 예측 결과 전송
# B/C에서 사용
# ================================
def send_status_to_master(pred_result):
    if IS_MASTER:
        return

    if not MASTER_NODE_IP:
        print("[STATUS] No REGRID_MASTER_IP configured.")
        return

    msg = {
        "node_id": NODE_ID,
        "fault_code": pred_result["fault_code"],
        "fault_name": pred_result["fault_name"],
        "confidence": pred_result["confidence"],
        "ia": pred_result["ia"],
        "ib": pred_result["ib"],
        "ic": pred_result["ic"],
        "temperature": pred_result["temperature"],
        "sound": pred_result["sound"],
        "timestamp": time.time(),
    }

    data = json.dumps(msg).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(data, (MASTER_NODE_IP, STATUS_PORT))
        print(
            f"[STATUS SEND] {NODE_ID} -> {MASTER_NODE_IP}:{STATUS_PORT} | "
            f"code={msg['fault_code']}({msg['fault_name']}), "
            f"conf={msg['confidence']:.3f}"
        )
    except Exception as e:
        print(f"[STATUS SEND ERROR] {e}")
    finally:
        sock.close()


# ================================
# A 노드에서 B/C 예측 결과 수신
# ================================
def receive_status_from_nodes():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", STATUS_PORT))

    print(f"[STATUS RECV] A node listening on port {STATUS_PORT}")

    while True:
        data, addr = sock.recvfrom(4096)

        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception as e:
            print(f"[STATUS RECV ERROR] json decode failed from {addr}: {e}")
            continue

        node_id = msg.get("node_id", None)

        if node_id not in latest_status:
            print(f"[STATUS RECV] unknown node_id={node_id} from {addr}")
            continue

        with status_lock:
            latest_status[node_id] = {
                "fault_code": int(msg.get("fault_code", 0)),
                "fault_name": msg.get("fault_name", "UNKNOWN"),
                "confidence": float(msg.get("confidence", 0.0)),
                "timestamp": time.time(),
            }

        print(
            f"[STATUS RECV] from {node_id} | "
            f"code={msg.get('fault_code')}({msg.get('fault_name')}), "
            f"conf={float(msg.get('confidence', 0.0)):.3f}"
        )

        # B/C 고장코드를 받자마자 A가 ESS + 분리 스위치 판단
        if IS_MASTER:
            control_ess_by_ai_status()


# ================================
# 자기 노드 상태 업데이트
# ================================
def update_own_status(pred_result):
    with status_lock:
        latest_status[NODE_ID] = {
            "fault_code": pred_result["fault_code"],
            "fault_name": pred_result["fault_name"],
            "confidence": pred_result["confidence"],
            "timestamp": time.time(),
        }


# ================================
# A 노드에서 ESS ON/OFF 판단
# ================================
def decide_ess_cmd():
    now = time.time()
    active_fault_nodes = []

    with status_lock:
        copied = dict(latest_status)

    for node_id, info in copied.items():
        fault_code = int(info["fault_code"])
        fault_name = info["fault_name"]
        confidence = float(info["confidence"])
        timestamp = float(info["timestamp"])

        # 아직 데이터가 안 온 노드는 판단에서 제외
        if timestamp <= 0:
            continue

        # 자기 노드 말고 B/C 데이터가 오래됐으면 제외
        if node_id != NODE_ID and now - timestamp > STALE_SEC:
            continue

        # confidence가 충분하고, fault_code가 0이 아니면 고장
        if fault_code != 0 and confidence >= CONF_THRESHOLD:
            active_fault_nodes.append((node_id, fault_code, fault_name, confidence))

    if len(active_fault_nodes) > 0:
        return 1, active_fault_nodes

    return 0, active_fault_nodes


# ================================
# Simulink로 single 1개 UDP 전송
# ================================
def send_single_to_simulink(value, port, label):
    if not ENABLE_SIMULINK_CONTROL:
        return

    if not SIMULINK_LAPTOP_IP:
        print(f"[{label}] No REGRID_SIMULINK_IP configured.")
        return

    fmt = ">f" if BYTE_ORDER == "big" else "<f"
    data = struct.pack(fmt, float(value))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        for _ in range(COMMAND_REPEAT):
            sock.sendto(data, (SIMULINK_LAPTOP_IP, port))
            time.sleep(0.01)

        print(
            f"[{label}] Sent value={float(value):.1f} x{COMMAND_REPEAT} "
            f"to {SIMULINK_LAPTOP_IP}:{port}"
        )

    except Exception as e:
        print(f"[{label} ERROR] {e}")

    finally:
        sock.close()


# ================================
# Simulink로 ESS ON/OFF 신호 전송
# ess_on=True  -> 1.0
# ess_on=False -> 0.0
# ================================
def send_ess_cmd_to_simulink(ess_on):
    value = 1.0 if ess_on else 0.0
    send_single_to_simulink(
        value=value,
        port=SIMULINK_ESS_PORT,
        label="ESS CTRL"
    )


# ================================
# Simulink로 Node C 분리 스위치 신호 전송
#
# switch_close=True  -> 1.0 = CLOSE
# switch_close=False -> 0.0 = OPEN
#
# 고장 발생 시 OPEN시키려면 switch_close=False
# 정상 복귀 시 CLOSE시키려면 switch_close=True
# ================================
def send_isolate_cmd_to_simulink(open_switch):
    # 너희 Simulink 기준:
    # 0.0 = CLOSE
    # 1.0 = OPEN
    value = 1.0 if open_switch else 0.0

    send_single_to_simulink(
        value=value,
        port=SIMULINK_ISOLATE_PORT,
        label="ISOLATE CTRL"
    )

# ================================
# A 노드에서 ESS + 분리 스위치 제어
# ================================
def control_ess_by_ai_status():
    global last_ess_cmd

    ess_cmd, active_fault_nodes = decide_ess_cmd()
    ess_on = ess_cmd == 1

    ess_changed = last_ess_cmd != ess_on
    last_ess_cmd = ess_on

    if SEND_ON_CHANGE_ONLY and not ess_changed:
        return

    if ess_on:
        print("[CONTROL] FAULT detected -> ESS ON + ISOLATE OPEN")

        for node_id, fault_code, fault_name, confidence in active_fault_nodes:
            print(
                f"  - {node_id}: code={fault_code}({fault_name}), "
                f"conf={confidence:.3f}"
            )

        # ESS 백업 투입
        send_ess_cmd_to_simulink(ess_on=True)

        # 고장 발생 시 Node C 분리 스위치 OPEN
        # 6003으로 1.0 전송
        send_isolate_cmd_to_simulink(open_switch=True)

    else:
        print("[CONTROL] All available nodes Normal -> ESS OFF + ISOLATE CLOSE")

        # ESS 백업 해제
        send_ess_cmd_to_simulink(ess_on=False)

        # 정상 상태에서는 Node C 분리 스위치 CLOSE
        # 6003으로 0.0 전송
        send_isolate_cmd_to_simulink(open_switch=False)

# ================================
# 다음 노드로 5개 값 그대로 forwarding
# Simulink가 A에만 보내는 구조
# A -> B -> C
# ================================
def send_values_to_next_node(values):
    if not NEXT_NODE_IP:
        return

    if len(values) < 5:
        print("[CHAIN] Not enough values to forward.")
        return

    send_values = values[:5]

    fmt = ">fffff" if BYTE_ORDER == "big" else "<fffff"
    data = struct.pack(fmt, *[float(v) for v in send_values])

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(data, (NEXT_NODE_IP, NEXT_NODE_PORT))
        print(
            f"[CHAIN] {NODE_ID} -> {NEXT_NODE_IP}:{NEXT_NODE_PORT} | "
            f"Ia={send_values[0]:.2f}, "
            f"Ib={send_values[1]:.2f}, "
            f"Ic={send_values[2]:.2f}, "
            f"Temp={send_values[3]:.2f}, "
            f"Sound={send_values[4]:.2f}"
        )
    except Exception as e:
        print(f"[CHAIN ERROR] failed to send to {NEXT_NODE_IP}:{NEXT_NODE_PORT} | {e}")
    finally:
        sock.close()


# ================================
# 상태 출력
# ================================
def print_master_status():
    now = time.time()

    with status_lock:
        copied = dict(latest_status)

    print("-----------------------------------")
    print("[MASTER STATUS]")

    for node_id in ["node-a", "node-b", "node-c"]:
        info = copied[node_id]
        timestamp = float(info["timestamp"])

        if timestamp <= 0:
            age_text = "no data"
        else:
            age_text = f"{now - timestamp:.2f}s ago"

        print(
            f"{node_id}: "
            f"code={info['fault_code']}({info['fault_name']}), "
            f"conf={info['confidence']:.3f}, "
            f"updated={age_text}"
        )

    ess_cmd, active_fault_nodes = decide_ess_cmd()
    print(f"ESS_CMD={ess_cmd}")

    if active_fault_nodes:
        print(f"ESS ON nodes: {active_fault_nodes}")
    else:
        print("ESS OFF reason: no active fault")


# ================================
# 메인 수신 루프
# ================================
def receive_values():
    global last_print_time

    model, feature_cols, label_names = load_ai_model()

    # A 노드는 B/C 상태 수신 스레드 시작
    if IS_MASTER:
        t = threading.Thread(target=receive_status_from_nodes, daemon=True)
        t.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))

    print("===================================")
    print("ReGrid AI Node Main")
    print(f"NODE_ID={NODE_ID}")
    print(f"IS_MASTER={IS_MASTER}")
    print(f"HOST={HOST}")
    print(f"PORT={PORT}")
    print(f"MODEL_PATH={MODEL_PATH}")
    print(f"MASTER_NODE_IP={MASTER_NODE_IP}")
    print(f"STATUS_PORT={STATUS_PORT}")
    print(f"NEXT_NODE_IP={NEXT_NODE_IP}")
    print(f"NEXT_NODE_PORT={NEXT_NODE_PORT}")
    print(f"SIMULINK_LAPTOP_IP={SIMULINK_LAPTOP_IP}")
    print(f"SIMULINK_ESS_PORT={SIMULINK_ESS_PORT}")
    print(f"SIMULINK_ISOLATE_PORT={SIMULINK_ISOLATE_PORT}")
    print(f"BYTE_ORDER={BYTE_ORDER}")
    print(f"DATA_MODE={DATA_MODE}")
    print(f"CONF_THRESHOLD={CONF_THRESHOLD}")
    print(f"STALE_SEC={STALE_SEC}")
    print(f"ENABLE_SIMULINK_CONTROL={ENABLE_SIMULINK_CONTROL}")
    print(f"SEND_ON_CHANGE_ONLY={SEND_ON_CHANGE_ONLY}")
    print(f"COMMAND_REPEAT={COMMAND_REPEAT}")
    print(f"DEBUG={DEBUG}")
    print("Expected packet: [Ia, Ib, Ic, Temperature, Sound]")
    print("Recommended packet length: 20 bytes = single 5 values")
    print("ESS command packet to Simulink: single 1 value, 4 bytes")
    print("Isolate command packet to Simulink: single 1 value, 4 bytes")
    print("===================================")

    while True:
        data, addr = sock.recvfrom(4096)

        try:
            values = decode_udp_values(data)
        except Exception as e:
            print(f"[ERROR] decode failed from {addr}: {e}")
            if DEBUG:
                print(f"[DEBUG] len={len(data)}, raw={data.hex()}")
            continue

        if len(values) < 5:
            print(f"[ERROR] need 5 values, got {len(values)} from {addr}")
            continue

        if DEBUG:
            preview = ", ".join([f"{v:.3f}" for v in values[:10]])
            print(f"[DEBUG] from {addr} | len={len(data)} | raw={data[:60].hex()}")
            print(f"[DEBUG] decoded values: [{preview}]")

        try:
            pred = predict_fault(values, model, feature_cols, label_names)
        except Exception as e:
            print(f"[ERROR] AI predict failed from {addr}: {e}")
            continue

        update_own_status(pred)

        print(
            f"[{NODE_ID}] from {addr} | "
            f"Ia={pred['ia']:.2f}, "
            f"Ib={pred['ib']:.2f}, "
            f"Ic={pred['ic']:.2f}, "
            f"TEMP={pred['temperature']:.2f}, "
            f"SOUND={pred['sound']:.2f}, "
            f"PRED={pred['fault_code']}({pred['fault_name']}), "
            f"CONF={pred['confidence']:.3f}"
        )

        # B/C는 A로 자기 예측 결과 전송
        if not IS_MASTER:
            send_status_to_master(pred)

        # A는 A/B/C 상태 기준으로 ESS + 분리 스위치 제어
        if IS_MASTER:
            control_ess_by_ai_status()

            now = time.time()
            if now - last_print_time >= PRINT_INTERVAL:
                print_master_status()
                last_print_time = now

        # 필요하면 다음 노드로 원본 센서값 전달
        send_values_to_next_node(values)


def main():
    try:
        receive_values()
    except KeyboardInterrupt:
        print("\n[EXIT] KeyboardInterrupt")


if __name__ == "__main__":
    main()
