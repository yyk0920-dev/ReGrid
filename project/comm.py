# comm.py - 네트워크 통신 모듈
# TCP 기반 피어 노드 간 메시지 교환
# Fault 발생 시 질의/응답 통신 지원

import json
import socket
import threading
import time
import queue
from config import HOST, PORT

MAX_RETRIES = 3
RETRY_DELAY = 1.0
MESSAGE_TIMEOUT = 5.0
MAX_MESSAGE_QUEUE_SIZE = 100
SOCKET_READ_TIMEOUT = 2.0


class MessageQueue:
    def __init__(self):
        self.queue = queue.Queue(maxsize=MAX_MESSAGE_QUEUE_SIZE)
        self.sent_messages = {}
        self.ack_received = set()

    def add_message(self, message, message_id=None):
        if message_id is None:
            message_id = f"{time.time()}_{threading.current_thread().ident}"
        try:
            self.queue.put((message_id, message), timeout=1.0)
            return message_id
        except queue.Full:
            print("[COMM] Message queue full, dropping message")
            return None

    def mark_sent(self, message_id, message):
        self.sent_messages[message_id] = (message, time.time(), 0)

    def mark_ack(self, message_id):
        if message_id in self.sent_messages:
            del self.sent_messages[message_id]
            self.ack_received.add(message_id)

    def get_expired_messages(self):
        current_time = time.time()
        return [
            (msg_id, msg, retries)
            for msg_id, (msg, timestamp, retries) in self.sent_messages.items()
            if current_time - timestamp > MESSAGE_TIMEOUT
        ]

    def increment_retry(self, message_id):
        if message_id in self.sent_messages:
            msg, timestamp, retries = self.sent_messages[message_id]
            self.sent_messages[message_id] = (msg, time.time(), retries + 1)

    def cleanup_old_acks(self, max_age=300.0):
        current_time = time.time()
        self.ack_received = {
            ack for ack in self.ack_received
            if current_time - float(ack.split('_')[0]) < max_age
        }


class PeerConnection:
    def __init__(self, peer_ip, port=PORT):
        self.peer_ip = peer_ip
        self.port = port
        self.socket = None
        self.is_connected = False
        self.connect_time = 0
        self.message_count = 0

    def connect(self):
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
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
        self.socket = None
        self.is_connected = False

    def send_message(self, message):
        if not self.is_connected:
            return False
        try:
            payload = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False)
            self.socket.sendall((payload + "\n").encode("utf-8"))
            self.message_count += 1
            return True
        except OSError as e:
            print(f"[COMM] Send failed to {self.peer_ip}: {e}")
            self.is_connected = False
            return False

    def receive_ack(self, timeout=1.0):
        if not self.is_connected:
            return None
        try:
            self.socket.settimeout(timeout)
            data = self.socket.recv(1024).decode("utf-8").strip()
            if data:
                ack_msg = json.loads(data)
                if ack_msg.get("type") == "ack":
                    return ack_msg.get("message_id")
        except (socket.timeout, json.JSONDecodeError):
            pass
        except OSError:
            self.is_connected = False
        finally:
            try:
                self.socket.settimeout(None)
            except Exception:
                pass
        return None


_message_queue = MessageQueue()
_peer_connections = {}


def get_peer_connection(peer_ip, port=PORT):
    key = f"{peer_ip}:{port}"
    if key not in _peer_connections:
        _peer_connections[key] = PeerConnection(peer_ip, port)
    return _peer_connections[key]


def send_message(message, target_ip, port=PORT, require_ack=False):
    peer = get_peer_connection(target_ip, port)
    outbound_message = message.copy() if isinstance(message, dict) else {"payload": message}
    message_id = f"{time.time()}_{threading.current_thread().ident}"
    outbound_message["message_id"] = message_id
    outbound_message["require_ack"] = require_ack

    message_id = _message_queue.add_message(outbound_message, message_id=message_id)
    if not message_id:
        return False

    for attempt in range(MAX_RETRIES):
        try:
            if not peer.is_connected and not peer.connect():
                time.sleep(RETRY_DELAY)
                continue

            if peer.send_message(outbound_message):
                _message_queue.mark_sent(message_id, outbound_message)
                if not require_ack:
                    return True
                ack_id = peer.receive_ack(timeout=1.0)
                if ack_id == message_id:
                    _message_queue.mark_ack(message_id)
                    return True
                print(f"[COMM] ACK timeout for message {message_id}")
        except Exception as e:
            print(f"[COMM] Send error to {target_ip} attempt={attempt + 1}: {e}")
            peer.disconnect()

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)

    return False


def request_response(message, target_ip, port=PORT, timeout=SOCKET_READ_TIMEOUT):
    """단발성 요청/응답 TCP 통신. FAULT_QUERY -> FAULT_REPLY에 사용."""
    outbound = message.copy() if isinstance(message, dict) else {"payload": message}
    outbound.setdefault("message_id", f"{time.time()}_{threading.current_thread().ident}")

    for attempt in range(MAX_RETRIES):
        try:
            with socket.create_connection((target_ip, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                payload = json.dumps(outbound, ensure_ascii=False) + "\n"
                sock.sendall(payload.encode("utf-8"))
                data = sock.recv(4096).decode("utf-8").strip()
                if not data:
                    return None
                return json.loads(data)
        except (OSError, socket.timeout, json.JSONDecodeError) as e:
            print(f"[COMM] request_response failed to {target_ip} attempt={attempt + 1}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return None


def broadcast_message(message, peers, require_ack=False):
    success_count = 0
    for peer_ip in peers:
        if send_message(message, peer_ip, require_ack=require_ack):
            success_count += 1
        else:
            print(f"[COMM] Broadcast failed to {peer_ip}")
    print(f"[COMM] Broadcast complete: {success_count}/{len(peers)} peers")
    return success_count


def send_json(client_socket, message):
    try:
        client_socket.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        return True
    except OSError as e:
        print(f"[COMM] send_json failed: {e}")
        return False


def send_ack(client_socket, message_id):
    return send_json(client_socket, {"type": "ack", "message_id": message_id, "timestamp": time.time()})


def start_server(on_message=None, host=HOST, port=PORT):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[COMM] TCP server started on {host}:{port}")

    while True:
        try:
            client, address = server.accept()
            threading.Thread(target=handle_client, args=(client, address, on_message), daemon=True).start()
        except OSError as e:
            print(f"[COMM] Server accept error: {e}")
            time.sleep(1.0)


def handle_client(client_socket, address, on_message):
    try:
        client_socket.settimeout(SOCKET_READ_TIMEOUT)
        buffer = ""
        while True:
            chunk = client_socket.recv(4096).decode("utf-8")
            if not chunk:
                break
            buffer += chunk

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[COMM] Invalid JSON from {address[0]}: {line}")
                    continue

                message_id = message.get("message_id")
                if message.get("require_ack") and message_id:
                    send_ack(client_socket, message_id)

                print(f"[COMM] Received from {address[0]}: {message}")
                if on_message:
                    on_message(message, address, client_socket)

                # request_response는 단발 요청이므로 응답 후 종료 가능
                if message.get("type") == "FAULT_QUERY":
                    return
    except socket.timeout:
        pass
    except OSError as e:
        print(f"[COMM] Client {address[0]} connection error: {e}")
    finally:
        try:
            client_socket.close()
        except Exception:
            pass


def start_server_thread(on_message=None, host=HOST, port=PORT):
    thread = threading.Thread(target=start_server, kwargs={"on_message": on_message, "host": host, "port": port}, daemon=True)
    thread.start()
    return thread


def start_message_processor():
    def processor():
        while True:
            try:
                for msg_id, msg, retries in _message_queue.get_expired_messages():
                    if retries < MAX_RETRIES:
                        _message_queue.increment_retry(msg_id)
                    else:
                        _message_queue.sent_messages.pop(msg_id, None)
                _message_queue.cleanup_old_acks()
                time.sleep(5.0)
            except Exception as e:
                print(f"[COMM] Message processor error: {e}")
                time.sleep(10.0)

    thread = threading.Thread(target=processor, daemon=True)
    thread.start()
    return thread


def get_communication_status():
    return {
        "active_connections": len([p for p in _peer_connections.values() if p.is_connected]),
        "total_peers": len(_peer_connections),
        "pending_messages": _message_queue.queue.qsize(),
        "sent_messages": len(_message_queue.sent_messages),
        "acked_messages": len(_message_queue.ack_received),
    }


_message_processor_thread = start_message_processor()
