# fault_detection.py

from datetime import datetime
from config import CURRENT_THRESHOLD, CURRENT_MIN


FAULT_CODES = {
    "NORMAL": 0,
    "OVERLOAD": 2,
    "DISCONNECT": 3,
}


FAULT_MESSAGES = {
    "NORMAL": "정상 상태",
    "OVERLOAD": "전류 과부하 감지",
    "DISCONNECT": "전류 미감지 또는 연결 끊김 감지",
}


def detect_fault(current, device="pi01"):
    """
    n8n MQTT Trigger → Switch 노드에서 바로 쓰기 좋은 결과 반환
    Switch 기준값: {{$json.code}}
    """

    if current > CURRENT_THRESHOLD:
        status = "OVERLOAD"
        level = "danger"
    elif current < CURRENT_MIN:
        status = "DISCONNECT"
        level = "warning"
    else:
        status = "NORMAL"
        level = "normal"

    return {
        "device": device,
        "code": FAULT_CODES[status],
        "status": status,
        "level": level,
        "current": current,
        "threshold": CURRENT_THRESHOLD,
        "min_current": CURRENT_MIN,
        "message": FAULT_MESSAGES[status],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }