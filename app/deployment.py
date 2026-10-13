"""Host and HTTPS origin settings for server deployments."""

import os
from urllib.parse import urlsplit


def network_config():
    origin = os.environ.get("SAIBOGIRL_PUBLIC_ORIGIN", "").strip().rstrip("/")
    hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
    extra_hosts = [host.strip() for host in os.environ.get("SAIBOGIRL_ALLOWED_HOSTS", "").split(",")
                   if host.strip()]
    if "*" in extra_hosts:
        hosts = ["*"]
    else:
        hosts.extend(extra_hosts)
    if origin:
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("SAIBOGIRL_PUBLIC_ORIGIN 必须是完整的 HTTPS 站点地址，例如 https://chat.example.com")
        hosts.append(parsed.hostname)
    return origin, hosts
