# sensor.py - 하드웨어 센서 인터페이스 모듈
# MCP3008 ADC + ZMPT101B(전압) + ACS712(전류) 센서 제어
# RMS 계산 및 이동 평균 필터링 지원

# TODO: 실제 하드웨어 연결 시 다음 항목들 검증 필요
# - ZMPT101B_SENSITIVITY: 센서 출력 전압 비율 (데이터시트 참조)
# - ACS712_SENSITIVITY: 0.185 V/A (ACS712-30A 기준)
# - VOLTAGE_DIVIDER_RATIO: 전압 분배 비율 (220V → 2.2V로 가정)
# - ADC_VREF: MCP3008 기준 전압 (일반적으로 3.3V)

# sensor
import math
from collections import deque

from config import FILTER_WINDOW_SIZE

try:
    import board
    import busio
    import adafruit_mcp3008
except ImportError:
    board = None
    busio = None
    adafruit_mcp3008 = None


_voltage_window = deque(maxlen=FILTER_WINDOW_SIZE)
_current_window = deque(maxlen=FILTER_WINDOW_SIZE)

# MCP3008 setup (SPI 인터페이스 사용)
_spi = None
_mcp = None
_channel_voltage = None
_channel_current = None

# Sensor calibration parameters
# ZMPT101B: AC 전압 측정 (RMS 값)
ZMPT101B_SENSITIVITY = 0.185  # V/V (출력 전압 대 입력 전압 비율)
ZMPT101B_OFFSET = 1.65  # 중간값 (VCC/2 = 3.3V/2, 무신호 상태 ADC 출력)
ZMPT101B_SAMPLES = 100  # RMS 계산을 위한 샘플 수
ZMPT101B_SAMPLE_DELAY = 0.0001  # 100us (50Hz AC 주기: 20ms)

# ACS712: 전류 측정 (양방향)
ACS712_SENSITIVITY = 0.185  # V/A (ACS712-30A 기준, 185 mV/A)
ACS712_OFFSET = 1.65  # 중간값 (VCC/2 = 3.3V/2, 무전류 상태)
ACS712_SAMPLES = 100  # RMS 계산을 위한 샘플 수

# MCP3008 ADC 범위
ADC_VREF = 3.3  # 기준 전압
ADC_BITS = 65535  # 16-bit 범위 (Adafruit 라이브러리는 16비트 값 반환)

# 전압 분배 비율 (ZMPT101B는 고압을 분배하여 ADC 입력)
VOLTAGE_DIVIDER_RATIO = 100  # 220V RMS → 2.2V ADC


def _init_mcp3008():
    """
    MCP3008 ADC 초기화 함수.
    
    SPI 인터페이스를 통해 MCP3008을 초기화하고
    전압/전류 센서 채널을 설정합니다.
    하드웨어가 없을 경우 dry-run 모드로 동작합니다.
    """
    global _spi, _mcp, _channel_voltage, _channel_current
    if _mcp is None and board is not None:
        try:
            # SPI 인터페이스 사용 (MCP3008은 SPI 통신 사용)
            _spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
            cs = board.D8  # Chip Select (GPIO 8, SPI CE0)
            _mcp = adafruit_mcp3008.MCP3008(_spi, cs)
            _channel_voltage = _mcp.channel[0]  # 채널 0: ZMPT101B 전압 센서
            _channel_current = _mcp.channel[1]  # 채널 1: ACS712 전류 센서
            print("[SENSOR] MCP3008 ADC initialized (SPI mode)")
        except Exception as e:
            print(f"[SENSOR] MCP3008 initialization failed: {e}")


def read_voltage():
    """
    ZMPT101B 전압 센서에서 RMS 전압을 읽음.
    여러 샘플을 수집하여 RMS 계산.
    """
    _init_mcp3008()
    
    if _channel_voltage is None:
        print("[DRY-RUN] ZMPT101B voltage sensor is not available")
        return 220.0
    
    try:
        # 여러 샘플 수집 (AC 신호 RMS 계산)
        samples = []
        for _ in range(ZMPT101B_SAMPLES):
            # `.voltage` 사용: 실제 전압값 반환 (더 정확)
            # 또는 `.value`로 0~65535 범위의 정수값 사용
            adc_value = _channel_voltage.value
            
            # ADC 값을 전압으로 변환 (0-65535 → 0-3.3V)
            adc_voltage = (adc_value / ADC_BITS) * ADC_VREF
            
            # 오프셋 제거 (무신호 상태 = 1.65V)
            centered_voltage = adc_voltage - ZMPT101B_OFFSET
            samples.append(centered_voltage)
        
        # RMS 계산: sqrt(sum(V^2) / N)
        sum_of_squares = sum(v ** 2 for v in samples)
        rms_voltage_adc = math.sqrt(sum_of_squares / len(samples))
        
        # ADC 신호 → 실제 전압 변환
        # RMS = (RMS_ADC / SENSITIVITY) * 전압분배비율
        vrms = (rms_voltage_adc / ZMPT101B_SENSITIVITY) * VOLTAGE_DIVIDER_RATIO
        
        # 음수 방지
        return max(vrms, 0)
    except Exception as e:
        print(f"[SENSOR] Voltage read error: {e}")
        return 220.0


def read_current():
    """
    ACS712 전류 센서에서 전류를 읽음.
    여러 샘플을 수집하여 RMS 계산.
    """
    _init_mcp3008()
    
    if _channel_current is None:
        print("[DRY-RUN] ACS712 current sensor is not available")
        return 2.0
    
    try:
        # 여러 샘플 수집 (AC 신호 RMS 계산)
        samples = []
        for _ in range(ACS712_SAMPLES):
            # `.value` 사용: 0~65535 범위의 정수값
            adc_value = _channel_current.value
            
            # ADC 값을 전압으로 변환 (0-65535 → 0-3.3V)
            adc_voltage = (adc_value / ADC_BITS) * ADC_VREF
            
            # 오프셋 제거 (무전류 상태 = 1.65V)
            centered_voltage = adc_voltage - ACS712_OFFSET
            samples.append(centered_voltage)
        
        # RMS 계산: sqrt(sum(V^2) / N)
        sum_of_squares = sum(v ** 2 for v in samples)
        rms_voltage_adc = math.sqrt(sum_of_squares / len(samples))
        
        # ADC 신호 → 실제 전류 변환
        # I = RMS_ADC / SENSITIVITY
        current_rms = rms_voltage_adc / ACS712_SENSITIVITY
        
        # 음수 방지 (절댓값)
        return abs(current_rms)
    except Exception as e:
        print(f"[SENSOR] Current read error: {e}")
        return 0.0


def get_power_data():
    raw_voltage = read_voltage()
    raw_current = read_current()

    _voltage_window.append(raw_voltage)
    _current_window.append(raw_current)

    voltage = sum(_voltage_window) / len(_voltage_window)
    current = sum(_current_window) / len(_current_window)

    return {
        "voltage": voltage,
        "current": current,
        "raw_voltage": raw_voltage,
        "raw_current": raw_current,
    }
