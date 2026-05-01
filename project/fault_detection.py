# fault_detection.py

from config import CURRENT_THRESHOLD, CURRENT_MIN

def detect_fault(current):
    if current > CURRENT_THRESHOLD:
        return "OVERLOAD"
    elif current < CURRENT_MIN:
        return "DISCONNECT"
    else:
        return "NORMAL"