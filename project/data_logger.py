import json
import time
from urllib import request

from config import HTTP_TIMEOUT_SEC, LOG_FILE, N8N_WEBHOOK_URL


def build_event(node_id, voltage, current, fault):
    return {
        "timestamp": time.time(),
        "node_id": node_id,
        "voltage": round(voltage, 3),
        "current": round(current, 3),
        "fault": fault,
    }


def append_jsonl(event, path=LOG_FILE):
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")


def send_to_n8n(event):
    if not N8N_WEBHOOK_URL:
        return False

    body = json.dumps(event).encode("utf-8")
    http_request = request.Request(
        N8N_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=HTTP_TIMEOUT_SEC) as response:
            return 200 <= response.status < 300
    except OSError as exc:
        print(f"n8n send failed: {exc}")
        return False
