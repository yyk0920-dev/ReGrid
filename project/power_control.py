# power_control.py

from relay import cut_main_power, switch_to_backup, restore_main_power, stop_backup

def handle_fault(fault_type):
    if fault_type == "OVERLOAD" or fault_type == "DISCONNECT":
        cut_main_power()
        switch_to_backup()
    else:
        restore_main_power()
        stop_backup()