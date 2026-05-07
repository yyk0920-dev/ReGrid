# test_publish.py

import json
import paho.mqtt.client as mqtt

from fault_detection import detect_fault_payload

BROKER_HOST = "localhost"
TOPIC = "factory/test"

client = mqtt.Client()
client.connect(BROKER_HOST, 1883, 60)

payload = detect_fault_payload(current=12.4, voltage=220, device="pi01")

print(payload)

client.publish(TOPIC, json.dumps(payload, ensure_ascii=False))
client.disconnect()