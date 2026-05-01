from config import RELAY_ACTIVE_HIGH, RELAY_BACKUP, RELAY_MAIN

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


def _active_level():
    return GPIO.HIGH if RELAY_ACTIVE_HIGH else GPIO.LOW


def _inactive_level():
    return GPIO.LOW if RELAY_ACTIVE_HIGH else GPIO.HIGH


def _write(pin, active):
    if GPIO is None:
        print(f"[DRY-RUN] relay pin {pin} -> {'ON' if active else 'OFF'}")
        return
    GPIO.output(pin, _active_level() if active else _inactive_level())


def setup_relays():
    if GPIO is None:
        print("[DRY-RUN] RPi.GPIO is not available; relay output is simulated")
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(RELAY_MAIN, GPIO.OUT, initial=_inactive_level())
    GPIO.setup(RELAY_BACKUP, GPIO.OUT, initial=_inactive_level())


def cleanup_relays():
    if GPIO is not None:
        GPIO.cleanup()


setup_relays()


def cut_main_power():
    print("Main power cut")
    _write(RELAY_MAIN, True)


def restore_main_power():
    print("Main power restored")
    _write(RELAY_MAIN, False)


def switch_to_backup():
    print("Backup source enabled")
    _write(RELAY_BACKUP, True)


def stop_backup():
    print("Backup source disabled")
    _write(RELAY_BACKUP, False)
