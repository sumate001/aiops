#!/usr/bin/env python3
"""
Ollama Proxy — bridge Docker containers to remote Ollama (Tailscale/LAN)

รัน script นี้บน host ก่อน docker compose up เพื่อให้ Perplexica container
เข้าถึง Ollama ผ่าน host.docker.internal:11434

Usage:
    python3 ollama-proxy.py                         # default: 100.94.37.18:11434
    python3 ollama-proxy.py 192.168.1.100 11434     # custom target
    python3 ollama-proxy.py localhost 11434         # local Ollama
"""

import socket
import threading
import sys
import os

LISTEN_HOST = "::"          # dual-stack IPv4+IPv6
LISTEN_PORT = 11434

TARGET_HOST = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OLLAMA_HOST", "100.94.37.18")
TARGET_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 11434


def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            src.close()
        except Exception:
            pass


def handle(client: socket.socket) -> None:
    try:
        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote.settimeout(30)
        remote.connect((TARGET_HOST, TARGET_PORT))
        threading.Thread(target=pipe, args=(client, remote), daemon=True).start()
        threading.Thread(target=pipe, args=(remote, client), daemon=True).start()
    except Exception as e:
        print(f"[proxy] connect to {TARGET_HOST}:{TARGET_PORT} failed: {e}")
        client.close()


def main() -> None:
    server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(50)
    print(f"[proxy] listening on :{LISTEN_PORT} → {TARGET_HOST}:{TARGET_PORT}")
    print("[proxy] Docker containers use: http://host.docker.internal:11434")
    print("[proxy] Press Ctrl+C to stop")
    while True:
        client, addr = server.accept()
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
