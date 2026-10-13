"""Keep model downloads offline; allow only explicitly selected API services."""

import ipaddress
import os
import socket


def enable_offline(allowed_host=None):
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        os.environ[key] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    if getattr(socket.socket.connect, "_local_only", False):
        if allowed_host:
            socket.socket.connect._allowed_hosts.add(allowed_host)
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo
    allowed_hosts = {allowed_host} if allowed_host else set()
    allowed_ips = set()

    def check(host, resolving=False):
        if host in ("localhost", b"localhost", None):
            return
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if resolving and host in allowed_hosts:
            return
        try:
            if ipaddress.ip_address(host).is_loopback or host in allowed_ips:
                return
        except ValueError:
            pass
        raise OSError("语音离线模式禁止访问未授权的外部地址")

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check(address[0])
        return original_connect_ex(sock, address)

    def resolve(host, *args, **kwargs):
        check(host, resolving=True)
        result = original_resolve(host, *args, **kwargs)
        if host in allowed_hosts:
            allowed_ips.update(item[4][0] for item in result)
        return result

    connect._local_only = True
    connect._allowed_hosts = allowed_hosts
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = resolve
