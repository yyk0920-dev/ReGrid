# power_control.py - 전력 제어 로직 모듈
# 결함 감지 시 메인/백업 전원 자동 전환
# 격리/복구 시퀀스 관리 및 안전 장치 포함

import time

from relay import (
    cut_main_power,
    restore_main_power,
    stop_backup,
    switch_to_backup,
    get_relay_status,
    emergency_stop,
)


FAULTS_REQUIRING_ISOLATION = {
    "OVERLOAD",
    "DISCONNECT",
    "UNDERVOLTAGE",
    "OVERVOLTAGE",
}

# Power control configuration
ISOLATION_STABILIZE_TIME = 0.5  # 격리 후 안정화 대기 시간 (초)
RECOVERY_STABILIZE_TIME = 0.5  # 복구 후 안정화 대기 시간 (초)


class PowerController:
    def __init__(self):
        self.is_isolated = False
        self.last_fault_type = None
        self.isolation_count = 0  # 격리 횟수 (진단용)
        self.recovery_count = 0   # 복구 횟수 (진단용)
    
    def handle_fault(self, fault_type):
        """
        결함 유형에 따라 전력 격리/복구 처리.
        
        Args:
            fault_type: 결함 유형 (NORMAL, OVERLOAD, etc.)
        
        Returns:
            작업 성공 여부 (True/False)
        """
        should_isolate = fault_type in FAULTS_REQUIRING_ISOLATION
        self.last_fault_type = fault_type
        
        # 격리 필요 상태
        if should_isolate and not self.is_isolated:
            print(f"[POWER-CTRL] Fault detected: {fault_type} -> Isolating main power")
            success = self._isolate_main_power()
            return success
        
        # 복구 필요 상태
        elif not should_isolate and self.is_isolated:
            print(f"[POWER-CTRL] Fault cleared: {fault_type} -> Restoring main power")
            success = self._restore_main_power()
            return success
        
        # 상태 변화 없음
        return True
    
    def _isolate_main_power(self):
        """
        메인 전원 격리 시퀀스.
        
        1. 메인 전원 차단
        2. 백업 전원 활성화
        3. 상태 안정화
        """
        try:
            # Step 1: 메인 전원 차단
            cut_success = cut_main_power()
            if not cut_success:
                print("[POWER-CTRL] ERROR: Failed to cut main power!")
                return False
            
            # Step 2: 백업 전원 활성화
            time.sleep(ISOLATION_STABILIZE_TIME)
            backup_success = switch_to_backup()
            if not backup_success:
                print("[POWER-CTRL] ERROR: Failed to switch to backup power!")
                print("[POWER-CTRL] WARNING: Both main and backup may be offline!")
                return False
            
            # Step 3: 상태 업데이트
            self.is_isolated = True
            self.isolation_count += 1
            
            print(f"[POWER-CTRL] Isolation complete (count: {self.isolation_count})")
            return True
            
        except Exception as e:
            print(f"[POWER-CTRL] EXCEPTION during isolation: {e}")
            return False
    
    def _restore_main_power(self):
        """
        메인 전원 복구 시퀀스.
        
        1. 백업 전원 비활성화
        2. 메인 전원 복구
        3. 상태 안정화
        """
        try:
            # Step 1: 백업 전원 비활성화
            backup_off_success = stop_backup()
            if not backup_off_success:
                print("[POWER-CTRL] WARNING: Failed to stop backup power!")
            
            # Step 2: 메인 전원 복구
            time.sleep(RECOVERY_STABILIZE_TIME)
            restore_success = restore_main_power()
            if not restore_success:
                print("[POWER-CTRL] ERROR: Failed to restore main power!")
                print("[POWER-CTRL] Reactivating backup power as fallback")
                switch_to_backup()
                return False
            
            # Step 3: 상태 업데이트
            self.is_isolated = False
            self.recovery_count += 1
            
            print(f"[POWER-CTRL] Recovery complete (count: {self.recovery_count})")
            return True
            
        except Exception as e:
            print(f"[POWER-CTRL] EXCEPTION during recovery: {e}")
            return False
    
    def emergency_shutdown(self):
        """
        긴급 종료: 모든 전원 OFF.
        """
        print("[POWER-CTRL] !!! EMERGENCY SHUTDOWN - All power OFF !!!")
        success = emergency_stop()
        self.is_isolated = False
        return success
    
    def get_status(self):
        """현재 전력 제어 상태 반환."""
        relay_status = get_relay_status()
        return {
            "is_isolated": self.is_isolated,
            "last_fault_type": self.last_fault_type,
            "isolation_count": self.isolation_count,
            "recovery_count": self.recovery_count,
            "relay_status": relay_status,
        }


_default_controller = PowerController()


def handle_fault(fault_type):
    """모듈 레벨 인터페이스."""
    return _default_controller.handle_fault(fault_type)


def get_power_status():
    """현재 전력 상태 조회."""
    return _default_controller.get_status()


def emergency_shutdown():
    """긴급 종료."""
    return _default_controller.emergency_shutdown()
