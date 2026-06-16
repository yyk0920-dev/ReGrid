import json
import os
import threading
from datetime import datetime
from urllib import request


DEFAULT_WEBHOOK_URL = "http://localhost:5678/webhook/regrid"
N8N_WEBHOOK_URL = os.environ.get("REGRID_N8N_WEBHOOK_URL", DEFAULT_WEBHOOK_URL)
N8N_TIMEOUT_SEC = float(os.environ.get("REGRID_N8N_TIMEOUT_SEC", "1.0"))


def get_webhook_url():
    return N8N_WEBHOOK_URL


def get_webhook_urls():
    return {
        "unified": N8N_WEBHOOK_URL,
    }


def build_regrid_payload(response):
    fault_code = int(response.get("fault_code", response.get("code", 0)) or 0)
    current = response.get("current", response.get("current_value"))

    if current is None:
        current = response.get("currents")

    fault_node = response.get(
        "fault_node",
        response.get("node", response.get("node_id", "unknown")),
    )

    return {
        "event_type": "fault_predict",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "current": current,
        "fault_code": fault_code,
        "fault_node": fault_node,
    }


def _post_payload(webhook_url, payload, label):
    if not webhook_url:
        return False

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=N8N_TIMEOUT_SEC) as response:
            ok = 200 <= response.status < 300
            print(
                f"[N8N:{label}] sent={ok}, status={response.status}, "
                f"fault_node={payload.get('fault_node')}, "
                f"fault_code={payload.get('fault_code')}",
                flush=True,
            )
            return ok
    except Exception as exc:
        print(f"[N8N:{label}] send failed: {exc}", flush=True)
        return False


def send_regrid_event(response):
    if response.get("action") in {"camera", "power"}:
        return None

    payload = build_regrid_payload(response)

    thread = threading.Thread(
        target=_post_payload,
        args=(N8N_WEBHOOK_URL, payload, "regrid"),
        daemon=True,
    )
    thread.start()

    return payload


def build_daily_report_payload(data=None):
    payload = {
        "event_type": "daily_report",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "request": "daily_report",
    }

    if isinstance(data, dict):
        payload.update(data)

    return payload


def send_daily_report_request(data=None):
    payload = build_daily_report_payload(data)
    ok = _post_payload(N8N_WEBHOOK_URL, payload, "regrid")
    return ok, payload
