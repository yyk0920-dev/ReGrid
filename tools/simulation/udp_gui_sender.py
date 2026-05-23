import socket
import tkinter as tk

UDP_IP = "127.0.0.1"
UDP_PORT = 5000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_fault(code, name):
    msg = str(code)
    sock.sendto(msg.encode("utf-8"), (UDP_IP, UDP_PORT))
    status_label.config(text=f"현재 상태: {name}")
    print(f"Sent: {msg} ({name})")

root = tk.Tk()
root.title("ReGrid Fault GUI")
root.geometry("650x750")
root.resizable(False, False)

title_label = tk.Label(
    root,
    text="ReGrid 고장 제어 GUI",
    font=("맑은 고딕", 22, "bold")
)
title_label.pack(pady=20)

status_label = tk.Label(
    root,
    text="현재 상태: RESET / 정상상태",
    font=("맑은 고딕", 14)
)
status_label.pack(pady=10)

button_frame = tk.Frame(root)
button_frame.pack(pady=15)

faults = [
    (1, "F1 : 3상 단락"),
    (2, "F2 : A-B 단락"),
    (3, "F3 : B-C 단락"),
    (4, "F4 : C-A 단락"),
    (5, "F5 : A상 지락"),
    (6, "F6 : B상 지락"),
    (7, "F7 : C상 지락"),
    (0, "RESET / N : 정상상태"),
]

for idx, (code, name) in enumerate(faults):
    row = idx // 2
    col = idx % 2

    bg_color = "#d9ead3" if code == 0 else "#f2f2f2"

    btn = tk.Button(
        button_frame,
        text=name,
        width=24,
        height=3,
        font=("맑은 고딕", 12),
        bg=bg_color,
        command=lambda c=code, n=name: send_fault(c, n)
    )
    btn.grid(row=row, column=col, padx=12, pady=10)

root.mainloop()