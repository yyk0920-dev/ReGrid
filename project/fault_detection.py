# fault_detection.py - n8n 연결 payload 지원

# fault_detection.py - 결함 감지 모듈
# 전압/전류 모니터링 및 결함 분류
# 히스테리시스 필터링 + n8n 페이로드 지원

from datetime import datetime

from config import (
    CURRENT_MIN,
    CURRENT_THRESHOLD,
    FAULT_CONFIRM_COUNT,
    NORMAL_CONFIRM_COUNT,
    VOLTAGE_MAX,
    VOLTAGE_MIN,
)


FAULT_CODES = {
    "NORMAL": 0,
    "UNDERVOLTAGE": 1,
    "OVERLOAD": 2,
    "DISCONNECT": 3,
    "OVERVOLTAGE": 4,
}


FAULT_LEVELS = {
    "NORMAL": "normal",
    "UNDERVOLTAGE": "warning",
    "OVERLOAD": "danger",
    "DISCONNECT": "warning",
    "OVERVOLTAGE": "danger",
}


FAULT_MESSAGES = {
    "NORMAL": "정상 상태",
    "UNDERVOLTAGE": "저전압 감지",
    "OVERLOAD": "전류 과부하 감지",
    "DISCONNECT": "전류 미감지 또는 연결 끊김 감지",
    "OVERVOLTAGE": "과전압 감지",
}


def classify_fault(voltage, current):
    """
    전압과 전류 값을 기반으로 결함을 분류합니다.
    
    Args:
        voltage (float): 측정된 전압 (V)
        current (float): 측정된 전류 (A)
    
    Returns:
        str: 결함 유형 ("NORMAL", "OVERLOAD", "DISCONNECT", "UNDERVOLTAGE", "OVERVOLTAGE")
    """
    if current > CURRENT_THRESHOLD:
        return "OVERLOAD"
    if current < CURRENT_MIN:
        return "DISCONNECT"
    if voltage < VOLTAGE_MIN:
        return "UNDERVOLTAGE"
    if voltage > VOLTAGE_MAX:
        return "OVERVOLTAGE"
    return "NORMAL"


def build_fault_payload(fault, current, voltage, device="pi01"):
    return {
        "device": device,
        "code": FAULT_CODES[fault],
        "status": fault,
        "level": FAULT_LEVELS[fault],
        "current": current,
        "voltage": voltage,
        "threshold": CURRENT_THRESHOLD,
        "min_current": CURRENT_MIN,
        "message": FAULT_MESSAGES[fault],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


class FaultDetector:
    def __init__(self):
        self.confirmed_fault = "NORMAL"
        self._candidate_fault = "NORMAL"
        self._candidate_count = 0
        self._normal_count = 0

    def detect(self, voltage, current):
        candidate = classify_fault(voltage, current)
        previous_fault = self.confirmed_fault

        if candidate == "NORMAL":
            self._normal_count += 1
            self._candidate_fault = "NORMAL"
            self._candidate_count = 0
            if self._normal_count >= NORMAL_CONFIRM_COUNT:
                self.confirmed_fault = "NORMAL"
        else:
            self._normal_count = 0
            if candidate == self._candidate_fault:
                self._candidate_count += 1
            else:
                self._candidate_fault = candidate
                self._candidate_count = 1

            if self._candidate_count >= FAULT_CONFIRM_COUNT:
                self.confirmed_fault = candidate

        # 상태 변화 로깅
        if self.confirmed_fault != previous_fault:
            level = FAULT_LEVELS.get(self.confirmed_fault, "unknown")
            message = FAULT_MESSAGES.get(self.confirmed_fault, "Unknown fault")
            print(f"[FAULT-DETECT] State changed: {previous_fault} → {self.confirmed_fault} "
                  f"(V={voltage:.1f}, I={current:.1f}) [{level}] {message}")

        return self.confirmed_fault

    def detect_payload(self, voltage, current, device="pi01"):
        fault = self.detect(voltage, current)
        return build_fault_payload(fault, current, voltage, device)


_default_detector = FaultDetector()


def detect_fault(current, voltage=220.0):
    return _default_detector.detect(voltage, current)


def detect_fault_payload(current, voltage=220.0, device="pi01"):
    fault = detect_fault(current, voltage)
    return build_fault_payload(fault, current, voltage, device)
