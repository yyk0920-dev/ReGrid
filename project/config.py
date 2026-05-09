"""Runtime configuration for the ReGrid 12V DC microgrid controller."""

import os


def _env_float(name, default):
    value = os.getenv(name)
    return default if value in (None, "") else float(value)


def _env_int(name, default):
    value = os.getenv(name)
    return default if value in (None, "") else int(value)


def _env_bool(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_csv(name):
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


# 12V DC bus protection thresholds. Tune these after measuring the real circuit.
NOMINAL_VOLTAGE = _env_float("REGRID_NOMINAL_VOLTAGE", 12.0)
CURRENT_THRESHOLD = _env_float("REGRID_CURRENT_MAX", 5.0)
CURRENT_MIN = _env_float("REGRID_CURRENT_MIN", 0.05)
VOLTAGE_MIN = _env_float("REGRID_VOLTAGE_MIN", 10.5)
VOLTAGE_MAX = _env_float("REGRID_VOLTAGE_MAX", 13.8)

SAMPLE_INTERVAL_SEC = _env_float("REGRID_SAMPLE_INTERVAL_SEC", 1.0)
FILTER_WINDOW_SIZE = _env_int("REGRID_FILTER_WINDOW_SIZE", 5)
FAULT_CONFIRM_COUNT = _env_int("REGRID_FAULT_CONFIRM_COUNT", 3)
NORMAL_CONFIRM_COUNT = _env_int("REGRID_NORMAL_CONFIRM_COUNT", 5)

# GPIO BCM pin numbers. Each Raspberry Pi can override these with env vars.
RELAY_NODE = _env_int("REGRID_RELAY_NODE", 17)
RELAY_BACKUP = _env_int("REGRID_RELAY_BACKUP", 27)
RELAY_SOURCE_1 = _env_int("REGRID_RELAY_SOURCE_1", 22)
RELAY_SOURCE_2 = _env_int("REGRID_RELAY_SOURCE_2", 23)
RELAY_ESS = _env_int("REGRID_RELAY_ESS", 24)
RELAY_ACTIVE_HIGH = _env_bool("REGRID_RELAY_ACTIVE_HIGH", True)

# Backward-compatible aliases used by older modules.
RELAY_MAIN = RELAY_NODE

NODE_ID = os.getenv("REGRID_NODE_ID", "node-a")
HOST = os.getenv("REGRID_HOST", "0.0.0.0")
PORT = _env_int("REGRID_PORT", 5000)
PEER_NODES = _env_csv("REGRID_PEER_NODES")

LOG_FILE = os.getenv("REGRID_LOG_FILE", "power_data.jsonl")
N8N_WEBHOOK_URL = os.getenv("REGRID_N8N_WEBHOOK_URL", "")
HTTP_TIMEOUT_SEC = _env_float("REGRID_HTTP_TIMEOUT_SEC", 2.0)

MQTT_BROKER_HOST = os.getenv("REGRID_MQTT_BROKER_HOST", "localhost")
MQTT_PORT = _env_int("REGRID_MQTT_PORT", 1883)
MQTT_TOPIC = os.getenv("REGRID_MQTT_TOPIC", "regrid/events")
TELEMETRY_INTERVAL = _env_float("REGRID_TELEMETRY_INTERVAL", 60.0)
