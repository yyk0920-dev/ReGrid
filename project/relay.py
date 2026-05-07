# relay.py - 릴레이 제어 모듈
# Raspberry Pi GPIO를 통한 전력 제어 릴레이 관리
# 상태 추적, 재시도 로직, 긴급 정지 기능 포함

import time
from datetime import datetime

from config import RELAY_ACTIVE_HIGH, RELAY_BACKUP, RELAY_MAIN

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


# Relay state tracking
class RelayState:
    """
    릴레이 상태 추적 클래스.
    
    메인/백업 전원 상태와 마지막 변경 시간을 기록합니다.
    디버깅과 모니터링에 사용됩니다.
    """
    def __init__(self):
        self.main_active = False  # 메인 전원 ON/OFF 상태
        self.backup_active = False  # 백업 전원 ON/OFF 상태
        self.last_change_time = {}  # 마지막 상태 변경 시간 기록
        self.is_dry_run = GPIO is None  # 하드웨어 시뮬레이션 모드
    
    def log_state(self):
        """현재 릴레이 상태를 로그로 출력합니다."""
        state_str = f"MAIN={'ON' if self.main_active else 'OFF'} BACKUP={'ON' if self.backup_active else 'OFF'}"
        if self.is_dry_run:
            print(f"[DRY-RUN] Relay state: {state_str}")
        else:
            print(f"[RELAY] State: {state_str}")


_relay_state = RelayState()

# Relay configuration
RELAY_SWITCH_DELAY = 0.5  # 릴레이 응답 시간 (초)
RELAY_MAX_RETRY = 3  # 최대 재시도 횟수
RELAY_RETRY_DELAY = 0.1  # 재시도 간격 (초)


def _active_level():
    if GPIO is None:
        return True
    return GPIO.HIGH if RELAY_ACTIVE_HIGH else GPIO.LOW


def _inactive_level():
    if GPIO is None:
        return False
    return GPIO.LOW if RELAY_ACTIVE_HIGH else GPIO.HIGH


def _write_with_retry(pin, active, retry_count=0):
    """
    릴레이 쓰기 (재시도 로직 포함).
    
    Args:
        pin: GPIO 핀 번호
        active: 활성화 여부 (True=ON, False=OFF)
        retry_count: 현재 재시도 횟수
    
    Returns:
        성공 여부 (True/False)
    """
    if GPIO is None:
        print(f"[DRY-RUN] relay pin {pin} -> {'ON' if active else 'OFF'}")
        return True
    
    try:
        level = _active_level() if active else _inactive_level()
        GPIO.output(pin, level)
        
        # 상태 변경 시간 기록
        _relay_state.last_change_time[pin] = time.time()
        
        print(f"[RELAY] GPIO {pin} set to {'ON' if active else 'OFF'} (attempt {retry_count + 1})")
        return True
        
    except RuntimeError as e:
        print(f"[RELAY] GPIO {pin} write failed: {e}")
        
        # 재시도 로직
        if retry_count < RELAY_MAX_RETRY:
            print(f"[RELAY] Retrying... ({retry_count + 1}/{RELAY_MAX_RETRY})")
            time.sleep(RELAY_RETRY_DELAY)
            return _write_with_retry(pin, active, retry_count + 1)
        else:
            print(f"[RELAY] ERROR: Failed to set GPIO {pin} after {RELAY_MAX_RETRY} retries")
            return False
    
    except Exception as e:
        print(f"[RELAY] CRITICAL ERROR on GPIO {pin}: {e}")
        return False


def setup_relays():
    """릴레이 초기화."""
    if GPIO is None:
        print("[DRY-RUN] RPi.GPIO is not available; relay output is simulated")
        return
    
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(RELAY_MAIN, GPIO.OUT, initial=_inactive_level())
        GPIO.setup(RELAY_BACKUP, GPIO.OUT, initial=_inactive_level())
        print("[RELAY] GPIO setup complete (MAIN={}, BACKUP={})".format(RELAY_MAIN, RELAY_BACKUP))
        _relay_state.log_state()
    except Exception as e:
        print(f"[RELAY] Setup failed: {e}")


def cleanup_relays():
    """릴레이 정리 (종료 시 호출)."""
    try:
        if GPIO is not None:
            # 안전 종료: 모든 릴레이 OFF
            _write_with_retry(RELAY_MAIN, False)
            _write_with_retry(RELAY_BACKUP, False)
            time.sleep(RELAY_SWITCH_DELAY)
            GPIO.cleanup()
            print("[RELAY] GPIO cleanup complete")
    except Exception as e:
        print(f"[RELAY] Cleanup error: {e}")


# Initial setup
setup_relays()


def cut_main_power():
    """메인 전원 차단."""
    print("[RELAY] >>> Main power CUT")
    success = _write_with_retry(RELAY_MAIN, True)
    if success:
        _relay_state.main_active = True
        _relay_state.log_state()
    else:
        print("[RELAY] WARNING: Main power cut may have failed!")
    return success


def restore_main_power():
    """메인 전원 복구."""
    print("[RELAY] >>> Main power RESTORE")
    success = _write_with_retry(RELAY_MAIN, False)
    if success:
        _relay_state.main_active = False
        _relay_state.log_state()
    else:
        print("[RELAY] WARNING: Main power restore may have failed!")
    return success


def switch_to_backup():
    """백업 전원 활성화."""
    print("[RELAY] >>> Backup power ON")
    success = _write_with_retry(RELAY_BACKUP, True)
    if success:
        _relay_state.backup_active = True
        _relay_state.log_state()
    else:
        print("[RELAY] WARNING: Backup power activation may have failed!")
    return success


def stop_backup():
    """백업 전원 비활성화."""
    print("[RELAY] >>> Backup power OFF")
    success = _write_with_retry(RELAY_BACKUP, False)
    if success:
        _relay_state.backup_active = False
        _relay_state.log_state()
    else:
        print("[RELAY] WARNING: Backup power deactivation may have failed!")
    return success


def get_relay_status():
    """현재 릴레이 상태 반환."""
    return {
        "main_active": _relay_state.main_active,
        "backup_active": _relay_state.backup_active,
        "last_change_time": _relay_state.last_change_time,
        "is_dry_run": _relay_state.is_dry_run,
    }


def emergency_stop():
    """긴급 정지: 모든 릴레이 OFF."""
    print("[RELAY] !!! EMERGENCY STOP - All relays OFF !!!")
    success_main = _write_with_retry(RELAY_MAIN, False)
    time.sleep(RELAY_SWITCH_DELAY)
    success_backup = _write_with_retry(RELAY_BACKUP, False)
    
    _relay_state.main_active = False
    _relay_state.backup_active = False
    _relay_state.log_state()
    
    return success_main and success_backup
