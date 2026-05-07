# data_logger.py - 데이터 로깅 및 전송 모듈
# JSONL 파일 로깅 + n8n 웹훅 연동
# 로그 로테이션, 재시도, 큐 기반 전송 지원

import json
import os
import shutil
import threading
import time
import queue
from datetime import datetime
from urllib import request

from config import HTTP_TIMEOUT_SEC, LOG_FILE, N8N_WEBHOOK_URL


# Logging configuration
MAX_LOG_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_BACKUP_FILES = 5  # 최대 백업 파일 수
N8N_MAX_RETRIES = 3
N8N_RETRY_DELAY = 2.0  # seconds
BATCH_SEND_INTERVAL = 5.0  # seconds
MAX_QUEUE_SIZE = 1000


class LogManager:
    """
    로그 파일 관리 클래스.
    
    파일 크기 기반 로테이션, 백업 정리, 디스크 사용량 모니터링을 담당합니다.
    로그 파일이 무한히 커지는 것을 방지합니다.
    """
    
    def __init__(self, log_file=LOG_FILE):
        self.log_file = log_file
        self.backup_count = 0
    
    def should_rotate(self):
        """로그 파일 로테이션 필요 여부 확인."""
        if not os.path.exists(self.log_file):
            return False
        return os.path.getsize(self.log_file) >= MAX_LOG_FILE_SIZE
    
    def rotate_log(self):
        """로그 파일 로테이션."""
        if not os.path.exists(self.log_file):
            return
        
        # 백업 파일명 생성 (timestamp 기반)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"{self.log_file}.{timestamp}.bak"
        
        try:
            shutil.move(self.log_file, backup_file)
            print(f"[LOGGER] Log rotated: {backup_file}")
            
            # 오래된 백업 파일 정리
            self.cleanup_old_backups()
            
        except OSError as e:
            print(f"[LOGGER] Log rotation failed: {e}")
    
    def cleanup_old_backups(self):
        """오래된 백업 파일 정리."""
        try:
            log_dir = os.path.dirname(self.log_file)
            base_name = os.path.basename(self.log_file)
            
            # 백업 파일 목록 찾기
            backup_files = []
            for filename in os.listdir(log_dir):
                if filename.startswith(base_name + ".") and filename.endswith(".bak"):
                    filepath = os.path.join(log_dir, filename)
                    mtime = os.path.getmtime(filepath)
                    backup_files.append((filepath, mtime))
            
            # 수정시간 기준 정렬 (오래된 순)
            backup_files.sort(key=lambda x: x[1])
            
            # 최대 개수 초과 시 삭제
            while len(backup_files) > MAX_BACKUP_FILES:
                old_file, _ = backup_files.pop(0)
                os.remove(old_file)
                print(f"[LOGGER] Removed old backup: {old_file}")
                
        except OSError as e:
            print(f"[LOGGER] Backup cleanup failed: {e}")
    
    def get_disk_usage(self):
        """디스크 사용량 확인."""
        try:
            stat = os.statvfs(os.path.dirname(self.log_file))
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_available * stat.f_frsize
            used_percent = ((total - free) / total) * 100
            return {
                "total_gb": total / (1024**3),
                "free_gb": free / (1024**3),
                "used_percent": used_percent
            }
        except OSError:
            return {"error": "Cannot get disk usage"}


class N8nSender:
    """n8n 웹훅 전송 관리자."""
    
    def __init__(self, webhook_url=N8N_WEBHOOK_URL):
        self.webhook_url = webhook_url
        self.send_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self.batch_buffer = []
        self.last_batch_send = 0
        self.stats = {
            "sent": 0,
            "failed": 0,
            "retries": 0
        }
    
    def send_event(self, event, retry_count=0):
        """단일 이벤트 전송."""
        if not self.webhook_url:
            return False
        
        body = json.dumps(event).encode("utf-8")
        http_request = request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        try:
            with request.urlopen(http_request, timeout=HTTP_TIMEOUT_SEC) as response:
                success = 200 <= response.status < 300
                if success:
                    self.stats["sent"] += 1
                    print(f"[N8N] Event sent successfully: {event.get('fault', 'NORMAL')}")
                else:
                    print(f"[N8N] HTTP error: {response.status}")
                    self.stats["failed"] += 1
                return success
                
        except OSError as exc:
            print(f"[N8N] Send failed (attempt {retry_count + 1}): {exc}")
            self.stats["failed"] += 1
            
            # 재시도 로직
            if retry_count < N8N_MAX_RETRIES:
                time.sleep(N8N_RETRY_DELAY)
                return self.send_event(event, retry_count + 1)
            else:
                print(f"[N8N] Max retries exceeded for event")
                return False
    
    def queue_event(self, event):
        """이벤트 큐에 추가."""
        try:
            self.send_queue.put(event, timeout=1.0)
            return True
        except queue.Full:
            print("[N8N] Send queue full, dropping event")
            return False
    
    def send_batch(self, events):
        """배치 전송 (미래 확장용)."""
        if not events:
            return
        
        # 현재는 개별 전송 (배치 API 지원 시 수정)
        success_count = 0
        for event in events:
            if self.send_event(event):
                success_count += 1
        
        print(f"[N8N] Batch sent: {success_count}/{len(events)} events")
    
    def process_queue(self):
        """큐 처리 (배경 스레드에서 호출)."""
        current_time = time.time()
        
        # 배치 전송 시간 확인
        if current_time - self.last_batch_send >= BATCH_SEND_INTERVAL:
            # 큐에서 이벤트 수집
            events = []
            while not self.send_queue.empty() and len(events) < 10:  # 최대 10개 배치
                try:
                    event = self.send_queue.get_nowait()
                    events.append(event)
                except queue.Empty:
                    break
            
            if events:
                self.send_batch(events)
                self.last_batch_send = current_time
    
    def get_stats(self):
        """전송 통계 반환."""
        return self.stats.copy()


# Global instances
_log_manager = LogManager()
_n8n_sender = N8nSender()


def build_event(node_id, voltage, current, fault):
    """이벤트 데이터 생성."""
    return {
        "timestamp": time.time(),
        "node_id": node_id,
        "voltage": round(voltage, 3),
        "current": round(current, 3),
        "fault": fault,
    }


def append_jsonl(event, path=LOG_FILE):
    """JSONL 파일에 이벤트 추가."""
    try:
        # 로테이션 체크
        if _log_manager.should_rotate():
            _log_manager.rotate_log()
        
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(event) + "\n")
        
        print(f"[LOGGER] Event logged: {event['fault']}")
        return True
        
    except OSError as e:
        print(f"[LOGGER] File write failed: {e}")
        return False


def send_to_n8n(event, immediate=True):
    """
    n8n으로 이벤트 전송.
    
    Args:
        event: 전송할 이벤트
        immediate: 즉시 전송 여부 (False면 큐에 추가)
    
    Returns:
        성공 여부
    """
    if not _n8n_sender.webhook_url:
        return False
    
    if immediate:
        return _n8n_sender.send_event(event)
    else:
        return _n8n_sender.queue_event(event)


def log_and_send(event, node_id="unknown"):
    """
    이벤트 로깅 및 전송 (통합 함수).
    
    Args:
        event: 이벤트 데이터
        node_id: 노드 ID
    
    Returns:
        (log_success, send_success)
    """
    # 로깅
    log_success = append_jsonl(event)
    
    # 전송 (실패해도 로깅은 유지)
    send_success = send_to_n8n(event)
    
    if not send_success:
        print(f"[LOGGER] Event queued for retry: {event['fault']}")
        # 큐에 추가하여 재시도
        _n8n_sender.queue_event(event)
    
    return log_success, send_success


def start_background_processor():
    """배경 처리 스레드 시작."""
    def processor():
        while True:
            try:
                _n8n_sender.process_queue()
                time.sleep(1.0)  # 1초마다 큐 처리
            except Exception as e:
                print(f"[LOGGER] Background processor error: {e}")
                time.sleep(5.0)
    
    thread = threading.Thread(target=processor, daemon=True)
    thread.start()
    return thread


def get_logger_status():
    """로거 상태 조회."""
    disk_usage = _log_manager.get_disk_usage()
    n8n_stats = _n8n_sender.get_stats()
    
    return {
        "log_file_size": os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0,
        "disk_usage": disk_usage,
        "n8n_stats": n8n_stats,
        "queue_size": _n8n_sender.send_queue.qsize(),
    }


# Start background processor
_background_thread = start_background_processor()
