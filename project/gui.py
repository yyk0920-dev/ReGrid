import tkinter as tk
from tkinter import ttk
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

def classify_fault(voltage, current):
    if current > 5.0:
        return 2, 0   # OVERLOAD
    elif current < 0.05:
        return 3, 0   # DISCONNECT
    elif voltage < 10.5:
        return 1, 0   # UNDERVOLTAGE
    elif voltage > 13.8:
        return 4, 0   # OVERVOLTAGE
    else:
        return 0, 1   # NORMAL

def update_simulation():
    Vrms = float(v_entry.get())
    Irms = float(i_entry.get())

    t = np.linspace(0, 0.1, 1000)
    v = Vrms * np.sqrt(2) * np.sin(2 * np.pi * 60 * t)
    i = Irms * np.sqrt(2) * np.sin(2 * np.pi * 60 * t)

    fault_code, relay = classify_fault(Vrms, Irms)

    fault_label.config(text=f"fault_code: {fault_code}")
    relay_label.config(text=f"relay: {relay}")

    ax.clear()
    ax.plot(t, v, label="Voltage")
    ax.plot(t, i, label="Current")
    ax.set_title("ReGrid Sine Wave Simulation")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Amplitude")
    ax.legend()
    ax.grid(True)

    canvas.draw()

root = tk.Tk()
root.title("ReGrid GUI Simulator")
root.geometry("800x600")

frame = ttk.Frame(root)
frame.pack(pady=10)

ttk.Label(frame, text="Vrms").grid(row=0, column=0)
v_entry = ttk.Entry(frame)
v_entry.grid(row=0, column=1)
v_entry.insert(0, "12")

ttk.Label(frame, text="Irms").grid(row=1, column=0)
i_entry = ttk.Entry(frame)
i_entry.grid(row=1, column=1)
i_entry.insert(0, "1")

ttk.Button(frame, text="Run Simulation", command=update_simulation).grid(row=2, column=0, columnspan=2, pady=10)

fault_label = ttk.Label(root, text="fault_code: -", font=("Arial", 14))
fault_label.pack()

relay_label = ttk.Label(root, text="relay: -", font=("Arial", 14))
relay_label.pack()

fig, ax = plt.subplots(figsize=(7, 4))
canvas = FigureCanvasTkAgg(fig, master=root)
canvas.get_tk_widget().pack()

root.mainloop()