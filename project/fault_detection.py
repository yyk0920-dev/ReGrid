# fault_detection.py - n8n 연결 payload 지원
# 결함 감지 모듈
# 전압/전류 모니터링 및 결함 분류
# 히스테리시스 필터링 + n8n 페이로드 지원
# 장애 발생 / 장애 변경 / 복구 이벤트 지원

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


EVENT_MESSAGES = {
    "FAULT": "고장 상태가 감지되었습니다",
    "FAULT_CHANGED": "고장 상태가 변경되었습니다",
    "RECOVERY": "고장 상태가 정상으로 복구되었습니다",
}


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


def build_fault_payload(fault, current, voltage, device="pi01", event=None, previous_fault=None):
    message = FAULT_MESSAGES[fault]

    if event == "RECOVERY":
        message = "전력 상태 정상 복구"
    elif event == "FAULT_CHANGED":
        message = f"고장 상태 변경: {previous_fault} → {fault}"
    elif event == "FAULT":
        message = FAULT_MESSAGES[fault]

    return {
        "device": device,
        "code": FAULT_CODES[fault],
        "status": fault,
        "level": FAULT_LEVELS[fault],
        "current": current,
        "voltage": voltage,
        "threshold": CURRENT_THRESHOLD,
        "min_current": CURRENT_MIN,
        "message": message,
        "event": event,
        "previous_status": previous_fault,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


class FaultDetector:
    def __init__(self):
        self.confirmed_fault = "NORMAL"
        self._candidate_fault = "NORMAL"
        self._candidate_count = 0
        self._normal_count = 0
        self.last_event = None
        self.previous_fault = "NORMAL"

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

        self.previous_fault = previous_fault
        self.last_event = None

        if self.confirmed_fault != previous_fault:
            if previous_fault == "NORMAL" and self.confirmed_fault != "NORMAL":
                self.last_event = "FAULT"

            elif previous_fault != "NORMAL" and self.confirmed_fault == "NORMAL":
                self.last_event = "RECOVERY"

            elif previous_fault != "NORMAL" and self.confirmed_fault != "NORMAL":
                self.last_event = "FAULT_CHANGED"

            level = FAULT_LEVELS.get(self.confirmed_fault, "unknown")
            message = FAULT_MESSAGES.get(self.confirmed_fault, "Unknown fault")

            print(
                f"[FAULT-DETECT] State changed: {previous_fault} → {self.confirmed_fault} "
                f"(V={voltage:.1f}, I={current:.1f}) [{level}] {message} "
                f"event={self.last_event}"
            )

        return self.confirmed_fault

    def detect_payload(self, voltage, current, device="pi01"):
        fault = self.detect(voltage, current)

        return build_fault_payload(
            fault=fault,
            current=current,
            voltage=voltage,
            device=device,
            event=self.last_event,
            previous_fault=self.previous_fault,
        )


_default_detector = FaultDetector()


def detect_fault(current, voltage=220.0):
    return _default_detector.detect(voltage, current)


def detect_fault_payload(current, voltage=220.0, device="pi01"):
    return _default_detector.detect_payload(voltage, current, device)