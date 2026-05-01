import json
import socket
import threading

from config import HOST, PORT


def send_message(message, target_ip, port=PORT, timeout=2.0):
    payload = message if isinstance(message, str) else json.dumps(message)

    with socket.create_connection((target_ip, port), timeout=timeout) as client:
        client.sendall(payload.encode("utf-8"))


def broadcast_message(message, peers):
    for peer_ip in peers:
        try:
            send_message(message, peer_ip)
        except OSError as exc:
            print(f"send failed to {peer_ip}: {exc}")


def start_server(on_message=None, host=HOST, port=PORT):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)

    print(f"TCP server started on {host}:{port}")

    while True:
        client, address = server.accept()
        with client:
            data = client.recv(4096).decode("utf-8")
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                message = {"raw": data}
            print(f"received from {address[0]}: {message}")
            if on_message:
                on_message(message, address)


def start_server_thread(on_message=None, host=HOST, port=PORT):
    thread = threading.Thread(
        target=start_server,
        kwargs={"on_message": on_message, "host": host, "port": port},
        daemon=True,
    )
    thread.start()
    return thread
