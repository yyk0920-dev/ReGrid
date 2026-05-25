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


NODE_ID = get_env("REGRID_NODE_ID", "node-a")
HOST = get_env("REGRID_HOST", "0.0.0.0")
PORT = int(get_env("REGRID_PORT", "5000"))

NEXT_NODE_IP = get_env("REGRID_CHAIN_NEXT_NODE", None)
NEXT_NODE_PORT = int(get_env("REGRID_CHAIN_NEXT_PORT", "5000"))

SIMULINK_LAPTOP_IP = get_env("REGRID_SIMULINK_IP", None)
SIMULINK_CONTROL_PORT = int(get_env("REGRID_SIMULINK_CONTROL_PORT", "6001"))

BYTE_ORDER = get_env("REGRID_BYTE_ORDER", "big")
DATA_MODE = get_env("REGRID_DATA_MODE", "auto")

ENABLE_SIMULINK_CONTROL = int(get_env("REGRID_ENABLE_SIMULINK_CONTROL", "1"))
DEBUG = int(get_env("REGRID_DEBUG", "1"))

# 1이면 fault가 바뀔 때만 Simulink로 제어 신호 전송
# 0이면 매 패킷마다 Simulink로 제어 신호 전송
# Simulink에서 보기에는 0 추천
SEND_ON_CHANGE_ONLY = int(get_env("REGRID_SEND_ON_CHANGE_ONLY", "0"))

# ================================
# 고장 기준값
# ================================
CURRENT_THRESHOLD = float(get_env("REGRID_CURRENT_THRESHOLD", "6.0"))
TEMP_THRESHOLD = float(get_env("REGRID_TEMP_THRESHOLD", "80.0"))
SOUND_THRESHOLD = float(get_env("REGRID_SOUND_THRESHOLD", "80.0"))

last_fault = None


# ================================
# Fault 이름
# ================================
def get_fault_name(fault):
    names = {
        0: "NORMAL",
        2: "OVERCURRENT",
        5: "HIGH_TEMP",
        6: "SPARK_SOUND",
        9: "MULTI_FAULT",
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
    [Ia, Ib, Ic, Temp, Sound]
    Data Type Conversion: single
    Mux input count: 5
    UDP Send → A RPi: 20 bytes

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
# Simulink로 차단기 제어 신호 전송
# 정상: 1.0
# 고장: 0.0
# ================================
def send_breaker_signal_to_simulink(closed):
    if not ENABLE_SIMULINK_CONTROL:
        return

    if not SIMULINK_LAPTOP_IP:
        print("[SIMULINK CTRL] No SIMULINK_LAPTOP_IP configured.")
        return

    value = 1.0 if closed else 0.0

    # Simulink UDP Receive 설정:
    # Data type: double
    # Data size: [1]
    # Byte order: Big Endian
    fmt = ">d" if BYTE_ORDER == "big" else "<d"
    data = struct.pack(fmt, value)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.sendto(data, (SIMULINK_LAPTOP_IP, SIMULINK_CONTROL_PORT))
        print(
            f"[SIMULINK CTRL] Sent breaker signal {value:.1f} "
            f"to {SIMULINK_LAPTOP_IP}:{SIMULINK_CONTROL_PORT}"
        )
    except Exception as e:
        print(f"[SIMULINK CTRL ERROR] {e}")
    finally:
        sock.close()


# ================================
# Fault 상태에 따라 Simulink 릴레이/차단기 제어
# ================================
def control_by_fault(fault):
    global last_fault

    changed = last_fault != fault
    last_fault = fault

    if SEND_ON_CHANGE_ONLY and not changed:
        return

    if fault == 0:
        if changed:
            print("[CONTROL] NORMAL -> Simulink breaker CLOSE")
        send_breaker_signal_to_simulink(closed=True)
    else:
        if changed:
            print(
                f"[CONTROL] FAULT -> Simulink breaker OPEN | "
                f"fault={fault}({get_fault_name(fault)})"
            )
        send_breaker_signal_to_simulink(closed=False)


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
            f"Ia={send_values[0]:.2f}, Ib={send_values[1]:.2f}, Ic={send_values[2]:.2f}, "
            f"Temp={send_values[3]:.2f}, Sound={send_values[4]:.2f}"
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
    print("ReGrid Multi-Fault Receiver")
    print(f"NODE_ID={NODE_ID}")
    print(f"HOST={HOST}")
    print(f"PORT={PORT}")
    print(f"NEXT_NODE_IP={NEXT_NODE_IP}")
    print(f"NEXT_NODE_PORT={NEXT_NODE_PORT}")
    print(f"SIMULINK_LAPTOP_IP={SIMULINK_LAPTOP_IP}")
    print(f"SIMULINK_CONTROL_PORT={SIMULINK_CONTROL_PORT}")
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

        control_by_fault(fault)

        if NODE_ID in ("node-a", "node-b"):
            send_values_to_next_node(values)


def main():
    try:
        receive_values()
    except KeyboardInterrupt:
        print("\n[EXIT] KeyboardInterrupt")


if __name__ == "__main__":
    main()
