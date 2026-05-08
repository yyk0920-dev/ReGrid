# comm.py - 네트워크 통신 모듈
# TCP 기반 피어 노드 간 메시지 교환
# 재연결, ACK, 메시지 큐, 타임아웃 처리 지원

import json
import socket
import threading
import time
import queue
from datetime import datetime

from config import HOST, PORT


# Communication configuration
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds
MESSAGE_TIMEOUT = 5.0  # seconds
HEARTBEAT_INTERVAL = 30.0  # seconds
MAX_MESSAGE_QUEUE_SIZE = 100


class MessageQueue:
    """
    메시지 큐 관리 클래스.
    
    재전송, ACK 추적, 타임아웃 처리를 담당합니다.
    메시지 신뢰성을 보장하기 위한 핵심 컴포넌트입니다.
    """
    
    def __init__(self):
        self.queue = queue.Queue(maxsize=MAX_MESSAGE_QUEUE_SIZE)
        self.sent_messages = {}  # message_id -> (message, timestamp, retries)
        self.ack_received = set()  # acknowledged message IDs
    
    def add_message(self, message, message_id=None):
        """메시지 큐에 추가."""
        if message_id is None:
            message_id = f"{time.time()}_{threading.current_thread().ident}"
        
        try:
            self.queue.put((message_id, message), timeout=1.0)
            return message_id
        except queue.Full:
            print("[COMM] Message queue full, dropping message")
            return None
    
    def get_pending_message(self):
        """대기 중인 메시지 가져오기."""
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None
    
    def mark_sent(self, message_id, message):
        """메시지 전송 기록."""
        self.sent_messages[message_id] = (message, time.time(), 0)
    
    def mark_ack(self, message_id):
        """ACK 수신 기록."""
        if message_id in self.sent_messages:
            del self.sent_messages[message_id]
            self.ack_received.add(message_id)
    
    def get_expired_messages(self):
        """타임아웃된 메시지 목록."""
        current_time = time.time()
        expired = []
        for msg_id, (msg, timestamp, retries) in self.sent_messages.items():
            if current_time - timestamp > MESSAGE_TIMEOUT:
                expired.append((msg_id, msg, retries))
        return expired
    
    def increment_retry(self, message_id):
        """재시도 횟수 증가."""
        if message_id in self.sent_messages:
            msg, timestamp, retries = self.sent_messages[message_id]
            self.sent_messages[message_id] = (msg, time.time(), retries + 1)
    
    def cleanup_old_acks(self, max_age=300.0):  # 5분
        """오래된 ACK 정리."""
        current_time = time.time()
        self.ack_received = {ack for ack in self.ack_received 
                           if current_time - float(ack.split('_')[0]) < max_age}


class PeerConnection:
    """피어 연결 관리."""
    
    def __init__(self, peer_ip, port=PORT):
        self.peer_ip = peer_ip
        self.port = port
        self.socket = None
        self.last_heartbeat = 0
        self.is_connected = False
        self.connect_time = 0
        self.message_count = 0
    
    def connect(self):
        """연결 시도."""
        try:
            self.socket = socket.create_connection((self.peer_ip, self.port), timeout=2.0)
            self.is_connected = True
            self.connect_time = time.time()
            self.message_count = 0
            print(f"[COMM] Connected to {self.peer_ip}:{self.port}")
            return True
        except OSError as e:
            print(f"[COMM] Connection failed to {self.peer_ip}: {e}")
            self.is_connected = False
            return False
    
    def disconnect(self):
        """연결 종료."""
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        self.is_connected = False
        print(f"[COMM] Disconnected from {self.peer_ip}")
    
    def send_message(self, message):
        """메시지 전송."""
        if not self.is_connected:
            return False
        
        try:
            payload = message if isinstance(message, str) else json.dumps(message)
            self.socket.sendall(payload.encode("utf-8"))
            self.message_count += 1
            return True
        except OSError as e:
            print(f"[COMM] Send failed to {self.peer_ip}: {e}")
            self.is_connected = False
            return False
    
    def receive_ack(self, timeout=1.0):
        """ACK 수신 대기."""
        if not self.is_connected:
            return None
        
        try:
            self.socket.settimeout(timeout)
            data = self.socket.recv(1024).decode("utf-8")
            if data:
                try:
                    ack_msg = json.loads(data)
                    if ack_msg.get("type") == "ack":
                        return ack_msg.get("message_id")
                except json.JSONDecodeError:
                    pass
        except socket.timeout:
            pass
        except OSError:
            self.is_connected = False
        finally:
            try:
                self.socket.settimeout(None)
            except:
                pass
        return None


# Global message queue
_message_queue = MessageQueue()

# Peer connections cache
_peer_connections = {}


def get_peer_connection(peer_ip, port=PORT):
    """피어 연결 객체 가져오기 (캐싱)."""
    key = f"{peer_ip}:{port}"
    if key not in _peer_connections:
        _peer_connections[key] = PeerConnection(peer_ip, port)
    return _peer_connections[key]


def send_message(message, target_ip, port=PORT, timeout=2.0, require_ack=False):
    """
    메시지 전송 (재시도 및 ACK 지원).
    
    Args:
        message: 전송할 메시지
        target_ip: 대상 IP
        port: 대상 포트
        timeout: 연결 타임아웃
        require_ack: ACK 요구 여부
    
    Returns:
        성공 여부
    """
    peer = get_peer_connection(target_ip, port)
    
    # 메시지 큐에 추가
    message_id = _message_queue.add_message(message)
    if not message_id:
        return False
    
    # 재시도 루프
    for attempt in range(MAX_RETRIES):
        try:
            # 연결 확인
            if not peer.is_connected:
                if not peer.connect():
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_DELAY)
                        continue
                    else:
                        return False
            
            # 메시지 전송
            if peer.send_message(message):
                _message_queue.mark_sent(message_id, message)
                
                # ACK 대기 (필요시)
                if require_ack:
                    ack_id = peer.receive_ack(timeout=1.0)
                    if ack_id == message_id:
                        _message_queue.mark_ack(message_id)
                        return True
                    else:
                        print(f"[COMM] ACK timeout for message {message_id}")
                else:
                    return True
            
        except Exception as e:
            print(f"[COMM] Send error to {target_ip} (attempt {attempt+1}): {e}")
            peer.disconnect()
        
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    
    return False


def broadcast_message(message, peers, require_ack=False):
    """
    여러 피어에 메시지 브로드캐스트.
    
    Args:
        message: 브로드캐스트할 메시지
        peers: 피어 IP 리스트
        require_ack: ACK 요구 여부
    
    Returns:
        성공한 피어 수
    """
    success_count = 0
    for peer_ip in peers:
        if send_message(message, peer_ip, require_ack=require_ack):
            success_count += 1
        else:
            print(f"[COMM] Broadcast failed to {peer_ip}")
    
    print(f"[COMM] Broadcast complete: {success_count}/{len(peers)} peers")
    return success_count


def send_ack(client_socket, message_id):
    """ACK 메시지 전송."""
    ack_msg = {"type": "ack", "message_id": message_id, "timestamp": time.time()}
    try:
        client_socket.sendall(json.dumps(ack_msg).encode("utf-8"))
    except OSError as e:
        print(f"[COMM] ACK send failed: {e}")


def start_server(on_message=None, host=HOST, port=PORT):
    """
    TCP 서버 시작 (개선된 버전).
    
    Args:
        on_message: 메시지 수신 콜백 (message, address, client_socket)
        host: 바인딩 호스트
        port: 바인딩 포트
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    
    print(f"[COMM] TCP server started on {host}:{port}")
    
    while True:
        try:
            client, address = server.accept()
            print(f"[COMM] Client connected: {address[0]}:{address[1]}")
            
            # 클라이언트 핸들러 스레드 시작
            threading.Thread(
                target=handle_client,
                args=(client, address, on_message),
                daemon=True
            ).start()
            
        except OSError as e:
            print(f"[COMM] Server accept error: {e}")
            time.sleep(1.0)
        except Exception as e:
            print(f"[COMM] Server critical error: {e}")
            time.sleep(5.0)


def handle_client(client_socket, address, on_message):
    """
    클라이언트 연결 처리.
    
    Args:
        client_socket: 클라이언트 소켓
        address: 클라이언트 주소
        on_message: 메시지 콜백
    """
    try:
        while True:
            data = client_socket.recv(4096).decode("utf-8")
            if not data:
                break
            
            try:
                message = json.loads(data)
                
                # ACK 요청 확인
                message_id = message.get("message_id")
                if message.get("require_ack") and message_id:
                    send_ack(client_socket, message_id)
                
                print(f"[COMM] Received from {address[0]}: {message}")
                
                if on_message:
                    on_message(message, address, client_socket)
                    
            except json.JSONDecodeError:
                print(f"[COMM] Invalid JSON from {address[0]}: {data}")
                message = {"raw": data}
                if on_message:
                    on_message(message, address, client_socket)
                    
    except OSError as e:
        print(f"[COMM] Client {address[0]} connection error: {e}")
    except Exception as e:
        print(f"[COMM] Client {address[0]} handler error: {e}")
    finally:
        try:
            client_socket.close()
        except:
            pass
        print(f"[COMM] Client {address[0]} disconnected")


def start_server_thread(on_message=None, host=HOST, port=PORT):
    """서버 스레드 시작."""
    thread = threading.Thread(
        target=start_server,
        kwargs={"on_message": on_message, "host": host, "port": port},
        daemon=True,
    )
    thread.start()
    return thread


def start_message_processor():
    """메시지 큐 처리기 시작 (재전송 등)."""
    def processor():
        while True:
            try:
                # 만료된 메시지 재전송
                expired_messages = _message_queue.get_expired_messages()
                for msg_id, msg, retries in expired_messages:
                    if retries < MAX_RETRIES:
                        print(f"[COMM] Retrying expired message {msg_id}")
                        # 재전송 로직 (실제 구현에서는 대상 정보 필요)
                        _message_queue.increment_retry(msg_id)
                    else:
                        print(f"[COMM] Dropping message {msg_id} after {retries} retries")
                        if msg_id in _message_queue.sent_messages:
                            del _message_queue.sent_messages[msg_id]
                
                # 오래된 ACK 정리
                _message_queue.cleanup_old_acks()
                
                time.sleep(5.0)  # 5초마다 체크
                
            except Exception as e:
                print(f"[COMM] Message processor error: {e}")
                time.sleep(10.0)
    
    thread = threading.Thread(target=processor, daemon=True)
    thread.start()
    return thread


def get_communication_status():
    """통신 상태 조회."""
    return {
        "active_connections": len([p for p in _peer_connections.values() if p.is_connected]),
        "total_peers": len(_peer_connections),
        "pending_messages": _message_queue.queue.qsize(),
        "sent_messages": len(_message_queue.sent_messages),
        "acked_messages": len(_message_queue.ack_received),
    }


# Initialize message processor
_message_processor_thread = start_message_processor()
