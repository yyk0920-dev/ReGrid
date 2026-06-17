import os
import socket
import struct
import math

# ================================
# 환경변수 읽기
# ================================
def get_env(name, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


# ================================
# 기본 설정
# ================================
NODE_ID = get_env("REGRID_NODE_ID", "node-a")

# 이 RPi가 Simulink 또는 이전 노드에서 데이터를 받는 포트
HOST = get_env("REGRID_HOST", "0.0.0.0")
PORT = int(get_env("REGRID_PORT", "5000"))

# 다음 RPi로 값 전달할 때 사용
NEXT_NODE_IP = get_env("REGRID_CHAIN_NEXT_NODE", None)
NEXT_NODE_PORT = int(get_env("REGRID_CHAIN_NEXT_PORT", "5000"))

# Simulink가 돌아가는 노트북 IP
SIMULINK_LAPTOP_IP = get_env("REGRID_SIMULINK_IP", None)

# Simulink에서 ESS 명령을 받는 UDP Receive 포트
SIMULINK_ESS_PORT = int(get_env("REGRID_SIMULINK_ESS_PORT", "6010"))

BYTE_ORDER = get_env("REGRID_BYTE_ORDER", "big")
DATA_MODE = get_env("REGRID_DATA_MODE", "auto")

ENABLE_SIMULINK_CONTROL = int(get_env("REGRID_ENABLE_SIMULINK_CONTROL", "1"))
DEBUG = int(get_env("REGRID_DEBUG", "1"))

# 1이면 ESS 명령이 바뀔 때만 전송
# 0이면 매 패킷마다 계속 전송
# Simulink에서 확실히 받게 하려면 일단 0 추천
SEND_ON_CHANGE_ONLY = int(get_env("REGRID_SEND_ON_CHANGE_ONLY", "0"))

# ================================
# 고장 기준값
# ================================
CURRENT_THRESHOLD = float(get_env("REGRID_CURRENT_THRESHOLD", "6.0"))
TEMP_THRESHOLD = float(get_env("REGRID_TEMP_THRESHOLD", "80.0"))
SOUND_THRESHOLD = float(get_env("REGRID_SOUND_THRESHOLD", "80.0"))

last_fault = None
last_ess_cmd = None


# ================================
# Fault 이름
# ================================
def get_fault_name(fault):
    names = {
        0: "NORMAL",

        # 기존 단순 판단용
        2: "OVERCURRENT",
        5: "HIGH_TEMP",
        6: "SPARK_SOUND",
        9: "MULTI_FAULT",

        # 나중에 AI 모델에서 0~7 고장코드 쓰는 경우 대비
        1: "F1_ABC_SHORT",
        3: "F3_BC_SHORT",
        4: "F4_CA_SHORT",
        7: "F7_C_GROUND",
    }
    return names.get(fault, "UNKNOWN")


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

    # auto 모드
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
# 5개 값 기준 고장 판단
# values = [Ia, Ib, Ic, Temperature, Sound]
# ================================
def classify_multi_fault(values):
    if len(values) < 5:
        raise ValueError(
            f"need 5 values: [Ia, Ib, Ic, Temperature, Sound], got {len(values)}"
        )

    ia = float(values[0])
    ib = float(values[1])
    ic = float(values[2])
    temperature = float(values[3])
    sound = float(values[4])

    all_values = [ia, ib, ic, temperature, sound]

    if not all(math.isfinite(v) for v in all_values):
        return {
            "fault": 9,
            "fault_name": "MULTI_FAULT",
            "ia": ia,
            "ib": ib,
            "ic": ic,
            "temperature": temperature,
            "sound": sound,
            "max_current": 0.0,
            "overcurrent": False,
            "high_temp": False,
            "spark_sound": False,
            "reason": "NaN_or_inf_detected",
        }

    max_current = max(abs(ia), abs(ib), abs(ic))

    overcurrent = max_current > CURRENT_THRESHOLD
    high_temp = temperature > TEMP_THRESHOLD
    spark_sound = sound > SOUND_THRESHOLD

    reason_list = []

    if overcurrent:
        reason_list.append("OVERCURRENT")

    if high_temp:
        reason_list.append("HIGH_TEMP")

    if spark_sound:
        reason_list.append("SPARK_SOUND")

    if len(reason_list) >= 2:
        fault = 9
    elif overcurrent:
        fault = 2
    elif high_temp:
        fault = 5
    elif spark_sound:
        fault = 6
    else:
        fault = 0
        reason_list.append("NORMAL")

    return {
        "fault": fault,
        "fault_name": get_fault_name(fault),
        "ia": ia,
        "ib": ib,
        "ic": ic,
        "temperature": temperature,
        "sound": sound,
        "max_current": max_current,
        "overcurrent": overcurrent,
        "high_temp": high_temp,
        "spark_sound": spark_sound,
        "reason": "+".join(reason_list),
    }


# ================================
# Simulink로 ESS ON/OFF 신호 전송
# ess_on=True  -> 1.0
# ess_on=False -> 0.0
# ================================
def send_ess_cmd_to_simulink(ess_on):
    if not ENABLE_SIMULINK_CONTROL:
        return

    if not SIMULINK_LAPTOP_IP:
        print("[ESS CTRL] No REGRID_SIMULINK_IP configured.")
        return

    value = 1.0 if ess_on else 0.0

    # Simulink UDP Receive 설정:
    # Data type: single
    # Data size: [1 1]
    # Byte order: Big-endian
    fmt = ">f" if BYTE_ORDER == "big" else "<f"
    data = struct.pack(fmt, value)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(data, (SIMULINK_LAPTOP_IP, SIMULINK_ESS_PORT))
        print(
            f"[ESS CTRL] Sent ess_cmd={value:.1f} "
            f"to {SIMULINK_LAPTOP_IP}:{SIMULINK_ESS_PORT}"
        )
    except Exception as e:
        print(f"[ESS CTRL ERROR] {e}")
    finally:
        sock.close()


# ================================
# Fault 상태에 따라 ESS 제어
# 현재 임시 로직:
# 정상 fault=0 -> ESS OFF
# 고장 fault!=0 -> ESS ON
#
# 나중에 AI/RPi 통신 결과가 들어오면
# 여기의 ess_on 결정 부분만 바꾸면 됨.
# ================================
def control_ess_by_fault(fault):
    global last_fault, last_ess_cmd

    fault_changed = last_fault != fault
    last_fault = fault

    # 임시 ESS 판단 로직
    ess_on = fault != 0

    ess_changed = last_ess_cmd != ess_on
    last_ess_cmd = ess_on

    if SEND_ON_CHANGE_ONLY and not ess_changed:
        return

    if ess_on:
        if fault_changed or ess_changed:
            print(
                f"[CONTROL] FAULT detected -> ESS ON | "
                f"fault={fault}({get_fault_name(fault)})"
            )
        send_ess_cmd_to_simulink(ess_on=True)
    else:
        if fault_changed or ess_changed:
            print("[CONTROL] NORMAL -> ESS OFF")
        send_ess_cmd_to_simulink(ess_on=False)


# ================================
# 다음 노드로 5개 값 그대로 forwarding
# ================================
def send_values_to_next_node(values):
    if not NEXT_NODE_IP:
        return

    if len(values) < 5:
        print("[CHAIN] Not enough values to forward.")
        return

    send_values = values[:5]

    # 체인 전달은 single 5개로 통일
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
# 메인 수신 루프
# ================================
def receive_values():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))

    print("===================================")
    print("ReGrid Node Receiver + ESS Control")
    print(f"NODE_ID={NODE_ID}")
    print(f"HOST={HOST}")
    print(f"PORT={PORT}")
    print(f"NEXT_NODE_IP={NEXT_NODE_IP}")
    print(f"NEXT_NODE_PORT={NEXT_NODE_PORT}")
    print(f"SIMULINK_LAPTOP_IP={SIMULINK_LAPTOP_IP}")
    print(f"SIMULINK_ESS_PORT={SIMULINK_ESS_PORT}")
    print(f"BYTE_ORDER={BYTE_ORDER}")
    print(f"DATA_MODE={DATA_MODE}")
    print(f"CURRENT_THRESHOLD={CURRENT_THRESHOLD}")
    print(f"TEMP_THRESHOLD={TEMP_THRESHOLD}")
    print(f"SOUND_THRESHOLD={SOUND_THRESHOLD}")
    print(f"ENABLE_SIMULINK_CONTROL={ENABLE_SIMULINK_CONTROL}")
    print(f"SEND_ON_CHANGE_ONLY={SEND_ON_CHANGE_ONLY}")
    print(f"DEBUG={DEBUG}")
    print("Expected packet: [Ia, Ib, Ic, Temperature, Sound]")
    print("Recommended packet length: 20 bytes = single 5 values")
    print("ESS command packet to Simulink: single 1 value, 4 bytes")
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

        if DEBUG:
            preview = ", ".join([f"{v:.3f}" for v in values[:10]])
            print(f"[DEBUG] from {addr} | len={len(data)} | raw={data[:60].hex()}")
            print(f"[DEBUG] decoded values: [{preview}]")

        try:
            result = classify_multi_fault(values)
        except Exception as e:
            print(f"[ERROR] classify failed from {addr}: {e}")
            continue

        fault = result["fault"]

        print(
            f"[{NODE_ID}] from {addr} | "
            f"Ia={result['ia']:.2f} A, "
            f"Ib={result['ib']:.2f} A, "
            f"Ic={result['ic']:.2f} A, "
            f"MAX_I={result['max_current']:.2f} A, "
            f"TEMP={result['temperature']:.2f}, "
            f"SOUND={result['sound']:.2f}, "
            f"FAULT={fault}({result['fault_name']}), "
            f"REASON={result['reason']}"
        )

        # A 노드만 Simulink로 ESS ON/OFF 명령 전송
        if NODE_ID == "node-a":
            control_ess_by_fault(fault)

        # A/B 노드는 다음 노드로 값 전달
        if NODE_ID in ("node-a", "node-b"):
            send_values_to_next_node(values)


def main():
    try:
        receive_values()
    except KeyboardInterrupt:
        print("\n[EXIT] KeyboardInterrupt")


if __name__ == "__main__":
    main()