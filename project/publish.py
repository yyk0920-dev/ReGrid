# publish.py

import json
import time
import paho.mqtt.client as mqtt

from fault_detection import detect_fault_payload

BROKER_HOST = "localhost"
TOPIC = "factory/test"

TELEMETRY_INTERVAL = 3600  # 1시간

client = mqtt.Client()
client.connect(BROKER_HOST, 1883, 60)

last_telemetry_time = 0

while True:
    # 나중에 여기만 실제 센서값으로 바꾸면 됨
    current = 3.1
    voltage = 220

    payload = detect_fault_payload(
        current=current,
        voltage=voltage,
        device="pi01"
    )

    now = time.time()

    # 고장/복구 알림은 기존처럼 event 있을 때만 즉시 전송
    if payload["event"] is not None:
        event_payload = payload.copy()
        event_payload["type"] = "event"

        client.publish(
            TOPIC,
            json.dumps(event_payload, ensure_ascii=False)
        )

    # 데일리 리포트용 데이터는 1시간마다 무조건 전송
    if now - last_telemetry_time >= TELEMETRY_INTERVAL:
        telemetry_payload = payload.copy()
        telemetry_payload["type"] = "telemetry"
        telemetry_payload["event"] = None

        client.publish(
            TOPIC,
            json.dumps(telemetry_payload, ensure_ascii=False)
        )

        print("[TELEMETRY SENT]", telemetry_payload)
        last_telemetry_time = now

    time.sleep(1)