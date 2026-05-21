import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import ttk

# PC에서 Simulink로 보낼 때는 127.0.0.1
# Raspberry Pi로 보낼 때는 HOST를 라즈베리파이 IP로 변경
HOST = "127.0.0.1"
PORT = 5000

current_vrms = 12.0
current_irms = 1.0
running = True

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def continuous_send():
    while running:
        msg = struct.pack(">ff", current_vrms, current_irms)
        sock.sendto(msg, (HOST, PORT))
        print("sending:", current_vrms, current_irms)
        time.sleep(0.01)


def update_value():
    global current_vrms, current_irms

    current_vrms = float(v_entry.get())
    current_irms = float(i_entry.get())

    status_label.config(
        text=f"Updated: Vrms={current_vrms}, Irms={current_irms}"
    )


root = tk.Tk()
root.title("ReGrid UDP GUI Sender")

ttk.Label(root, text="Vrms").pack()
v_entry = ttk.Entry(root)
v_entry.insert(0, "12")
v_entry.pack()

ttk.Label(root, text="Irms").pack()
i_entry = ttk.Entry(root)
i_entry.insert(0, "1")
i_entry.pack()

ttk.Button(root, text="Send to Simulink/RPi", command=update_value).pack(pady=10)

status_label = ttk.Label(root, text="Waiting...")
status_label.pack()

threading.Thread(target=continuous_send, daemon=True).start()

root.mainloop()