"""Publish ReGrid telemetry and fault events over MQTT."""

import json
import time
import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER_HOST,
    MQTT_PORT,
    MQTT_TOPIC,
    NODE_ID,
    TELEMETRY_INTERVAL,
)
from fault_detection import detect_fault_payload
from sensor import get_power_data


def build_mqtt_client():
    client = mqtt.Client()
    client.connect(MQTT_BROKER_HOST, MQTT_PORT, 60)
    client.loop_start()
    return client


def main():
    client = build_mqtt_client()
    last_telemetry_time = 0

    while True:
        data = get_power_data()

        current = data["current"]
        voltage = data["voltage"]

        payload = detect_fault_payload(
            current=current,
            voltage=voltage,
            device=NODE_ID,
        )

        payload["node_id"] = NODE_ID
        payload["raw_current"] = data.get("current_raw", 0)
        payload["raw_voltage"] = data.get("voltage_raw", 0)
        payload["sensor_fault"] = data.get("fault_text", "UNKNOWN")

        now = time.time()

        if payload["event"] is not None:
            event_payload = payload.copy()
            event_payload["type"] = "event"
            client.publish(MQTT_TOPIC, json.dumps(event_payload, ensure_ascii=False))
            print("[MQTT EVENT]", event_payload)

        if now - last_telemetry_time >= TELEMETRY_INTERVAL:
            telemetry_payload = payload.copy()
            telemetry_payload["type"] = "telemetry"
            telemetry_payload["event"] = None
            client.publish(MQTT_TOPIC, json.dumps(telemetry_payload, ensure_ascii=False))
            print("[MQTT TELEMETRY]", telemetry_payload)
            last_telemetry_time = now

        time.sleep(1)


if __name__ == "__main__":
    main()