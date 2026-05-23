import socket

from flask import Flask, jsonify, render_template

app = Flask(__name__)

UDP_IP = "127.0.0.1"
UDP_PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

current_state = {
    "code": 0,
    "label": "RESET",
    "desc": "정상상태",
}

faults = {
    0: ("RESET", "정상상태"),
    1: ("F1", "3상 단락"),
    2: ("F2", "A-B 단락"),
    3: ("F3", "B-C 단락"),
    4: ("F4", "C-A 단락"),
    5: ("F5", "A상 지락"),
    6: ("F6", "B상 지락"),
    7: ("F7", "C상 지락"),
    8: ("TEMP", "온도 높음"),
    9: ("SPARK", "스파크 감지 / 화재+소리"),
}


@app.route("/")
def index():
    return render_template("index.html", faults=faults, state=current_state)


@app.route("/fault/<int:code>", methods=["POST"])
def send_fault(code):
    if code not in faults:
        return jsonify({"ok": False, "error": "Invalid fault code"}), 400

    label, desc = faults[code]

    msg = str(code)
    sock.sendto(msg.encode("utf-8"), (UDP_IP, UDP_PORT))

    current_state["code"] = code
    current_state["label"] = label
    current_state["desc"] = desc

    print(f"Sent UDP: {msg} ({label}: {desc})")

    return jsonify({
        "ok": True,
        "code": code,
        "label": label,
        "desc": desc,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
