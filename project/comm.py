# comm.py

import socket

def send_message(message, target_ip, port=5000):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((target_ip, port))
    s.send(message.encode())
    s.close()

def start_server(port=5000):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('0.0.0.0', port))
    server.listen(5)

    print("📡 서버 시작")

    while True:
        client, addr = server.accept()
        data = client.recv(1024).decode()
        print(f"📩 수신: {data}")
        client.close()