"""Relay control for node isolation, source selection, and ESS backup."""

import time

from config import (
    RELAY_ACTIVE_HIGH,
    RELAY_BACKUP,
    RELAY_ESS,
    RELAY_NODE,
    RELAY_SOURCE_1,
    RELAY_SOURCE_2,
)

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


RELAY_SWITCH_DELAY = 0.5
RELAY_MAX_RETRY = 3
RELAY_RETRY_DELAY = 0.1

RELAY_PINS = {
    "node": RELAY_NODE,
    "backup": RELAY_BACKUP,
    "source_1": RELAY_SOURCE_1,
    "source_2": RELAY_SOURCE_2,
    "ess": RELAY_ESS,
}


class RelayState:
    def __init__(self):
        self.states = {name: False for name in RELAY_PINS}
        self.last_change_time = {}
        self.is_dry_run = GPIO is None

    @property
    def main_active(self):
        return self.states["node"]

    @main_active.setter
    def main_active(self, value):
        self.states["node"] = value

    @property
    def backup_active(self):
        return self.states["backup"] or self.states["ess"]

    @backup_active.setter
    def backup_active(self, value):
        self.states["backup"] = value

    def log_state(self):
        state = " ".join(
            f"{name.upper()}={'ON' if active else 'OFF'}"
            for name, active in self.states.items()
        )
        prefix = "[DRY-RUN]" if self.is_dry_run else "[RELAY]"
        print(f"{prefix} Relay state: {state}")


_relay_state = RelayState()


def _active_level():
    if GPIO is None:
        return True
    return GPIO.HIGH if RELAY_ACTIVE_HIGH else GPIO.LOW


def _inactive_level():
    if GPIO is None:
        return False
    return GPIO.LOW if RELAY_ACTIVE_HIGH else GPIO.HIGH


def _write_with_retry(pin, active, retry_count=0):
    if GPIO is None:
        print(f"[DRY-RUN] relay pin {pin} -> {'ON' if active else 'OFF'}")
        return True

    try:
        level = _active_level() if active else _inactive_level()
        GPIO.output(pin, level)
        _relay_state.last_change_time[pin] = time.time()
        print(f"[RELAY] GPIO {pin} set to {'ON' if active else 'OFF'}")
        return True
    except RuntimeError as exc:
        print(f"[RELAY] GPIO {pin} write failed: {exc}")
        if retry_count < RELAY_MAX_RETRY:
            time.sleep(RELAY_RETRY_DELAY)
            return _write_with_retry(pin, active, retry_count + 1)
        return False
    except Exception as exc:
        print(f"[RELAY] Critical error on GPIO {pin}: {exc}")
        return False


def setup_relays():
    if GPIO is None:
        print("[DRY-RUN] RPi.GPIO is not available; relay output is simulated")
        return

    try:
        GPIO.setmode(GPIO.BCM)
        for pin in set(RELAY_PINS.values()):
            GPIO.setup(pin, GPIO.OUT, initial=_inactive_level())
        print(f"[RELAY] GPIO setup complete: {RELAY_PINS}")
        _relay_state.log_state()
    except Exception as exc:
        print(f"[RELAY] Setup failed: {exc}")


def set_relay(name, active):
    if name not in RELAY_PINS:
        raise ValueError(f"Unknown relay name: {name}")

    pin = RELAY_PINS[name]
    success = _write_with_retry(pin, active)
    if success:
        _relay_state.states[name] = active
        _relay_state.last_change_time[name] = time.time()
        _relay_state.log_state()
    return success


def cut_main_power():
    print("[RELAY] >>> Node relay OPEN / isolate local section")
    return set_relay("node", True)


def restore_main_power():
    print("[RELAY] >>> Node relay CLOSE / restore local section")
    return set_relay("node", False)


def switch_to_backup():
    print("[RELAY] >>> ESS backup ON")
    return set_relay("ess", True)


def stop_backup():
    print("[RELAY] >>> ESS backup OFF")
    return set_relay("ess", False)


def enable_source_1():
    return set_relay("source_1", True)


def disable_source_1():
    return set_relay("source_1", False)


def enable_source_2():
    return set_relay("source_2", True)


def disable_source_2():
    return set_relay("source_2", False)


def get_relay_status():
    return {
        "states": _relay_state.states.copy(),
        "main_active": _relay_state.main_active,
        "backup_active": _relay_state.backup_active,
        "last_change_time": _relay_state.last_change_time.copy(),
        "is_dry_run": _relay_state.is_dry_run,
        "pins": RELAY_PINS.copy(),
    }


def emergency_stop():
    print("[RELAY] !!! EMERGENCY STOP - All relays OFF !!!")
    ok = True
    for name in RELAY_PINS:
        ok = set_relay(name, False) and ok
        time.sleep(RELAY_SWITCH_DELAY)
    return ok


def cleanup_relays():
    try:
        emergency_stop()
        if GPIO is not None:
            GPIO.cleanup()
            print("[RELAY] GPIO cleanup complete")
    except Exception as exc:
        print(f"[RELAY] Cleanup error: {exc}")


setup_relays()
