from config import (
    CURRENT_MIN,
    CURRENT_THRESHOLD,
    FAULT_CONFIRM_COUNT,
    NORMAL_CONFIRM_COUNT,
    VOLTAGE_MAX,
    VOLTAGE_MIN,
)


def classify_fault(voltage, current):
    if current > CURRENT_THRESHOLD:
        return "OVERLOAD"
    if current < CURRENT_MIN:
        return "DISCONNECT"
    if voltage < VOLTAGE_MIN:
        return "UNDERVOLTAGE"
    if voltage > VOLTAGE_MAX:
        return "OVERVOLTAGE"
    return "NORMAL"


class FaultDetector:
    def __init__(self):
        self.confirmed_fault = "NORMAL"
        self._candidate_fault = "NORMAL"
        self._candidate_count = 0
        self._normal_count = 0

    def detect(self, voltage, current):
        candidate = classify_fault(voltage, current)

        if candidate == "NORMAL":
            self._normal_count += 1
            self._candidate_fault = "NORMAL"
            self._candidate_count = 0
            if self._normal_count >= NORMAL_CONFIRM_COUNT:
                self.confirmed_fault = "NORMAL"
            return self.confirmed_fault

        self._normal_count = 0
        if candidate == self._candidate_fault:
            self._candidate_count += 1
        else:
            self._candidate_fault = candidate
            self._candidate_count = 1

        if self._candidate_count >= FAULT_CONFIRM_COUNT:
            self.confirmed_fault = candidate

        return self.confirmed_fault


_default_detector = FaultDetector()


def detect_fault(current, voltage=220.0):
    return _default_detector.detect(voltage, current)
