import socket
import struct

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

UDP_IP = "127.0.0.1"
UDP_PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

current_state = {
    "label": "RESET",
    "desc": "정상상태",
    "voltage": 12.0,
    "current": 1.0,
}

# code: (label, desc, voltage, current)
# 여기 값은 네가 원하는 임의의 V, I 값으로 바꾸면 됨
faults = {
    0: ("RESET", "정상상태", 12.0, 1.0),
    1: ("F1", "3상 단락", 6.0, 8.0),
    2: ("F2", "A-B 단락", 8.0, 6.5),
    3: ("F3", "B-C 단락", 8.5, 6.0),
    4: ("F4", "C-A 단락", 8.0, 6.0),
    5: ("F5", "A상 지락", 9.0, 4.5),
    6: ("F6", "B상 지락", 9.2, 4.2),
    7: ("F7", "C상 지락", 9.5, 4.0),
    8: ("TEMP", "온도 높음", 12.0, 3.0),
    9: ("SPARK", "스파크 감지 / 화재+소리", 5.0, 7.0),
}


def send_vi_udp(voltage, current):
    """
    voltage, current를 float 2개로 UDP 전송.
    Simulink UDP Receive:
    - Data type: single
    - Data size: [1 2]
    """
    msg = struct.pack(">ff", float(voltage), float(current))
    sock.sendto(msg, (UDP_IP, UDP_PORT))


@app.route("/")
def index():
    return render_template("index.html", faults=faults, state=current_state)


@app.route("/preset/<int:code>", methods=["POST"])
def send_preset(code):
    if code not in faults:
        return jsonify({"ok": False, "error": "Invalid preset code"}), 400

    label, desc, voltage, current = faults[code]

    send_vi_udp(voltage, current)

    current_state["label"] = label
    current_state["desc"] = desc
    current_state["voltage"] = voltage
    current_state["current"] = current

    print(f"Sent UDP: {label} / V={voltage}, I={current}")

    return jsonify({
        "ok": True,
        "label": label,
        "desc": desc,
        "voltage": voltage,
        "current": current
    })


@app.route("/manual", methods=["POST"])
def send_manual():
    data = request.get_json()

    try:
        voltage = float(data.get("voltage"))
        current = float(data.get("current"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid voltage/current"}), 400

    send_vi_udp(voltage, current)

    current_state["label"] = "MANUAL"
    current_state["desc"] = "직접 입력"
    current_state["voltage"] = voltage
    current_state["current"] = current

    print(f"Sent UDP: MANUAL / V={voltage}, I={current}")

    return jsonify({
        "ok": True,
        "label": "MANUAL",
        "desc": "직접 입력",
        "voltage": voltage,
        "current": current
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
