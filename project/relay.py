# relay.py

import RPi.GPIO as GPIO
from config import RELAY_MAIN, RELAY_BACKUP

GPIO.setmode(GPIO.BCM)
GPIO.setup(RELAY_MAIN, GPIO.OUT)
GPIO.setup(RELAY_BACKUP, GPIO.OUT)

def cut_main_power():
    print("⚡ 메인 전력 차단")
    GPIO.output(RELAY_MAIN, GPIO.HIGH)

def restore_main_power():
    print("⚡ 메인 전력 복구")
    GPIO.output(RELAY_MAIN, GPIO.LOW)

def switch_to_backup():
    print("🔋 ESS/태양광 전환")
    GPIO.output(RELAY_BACKUP, GPIO.HIGH)

def stop_backup():
    GPIO.output(RELAY_BACKUP, GPIO.LOW)