import socket
import struct

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 6008))

print("listening on 6008")

while True:
    data, addr = sock.recvfrom(1024)
    print("from", addr, "bytes", len(data))
    if len(data) >= 8:
        print(struct.unpack("!2f", data[:8]))