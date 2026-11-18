"""Accept only Caddy's overwritten client header from explicitly trusted transport peers."""

import ipaddress
import os


def client_address(request):
    peer = request.client.host if request.client else "unknown"
    return trusted_client_address(peer, request.headers)


def trusted_client_address(peer, headers):
    networks = [
        ipaddress.ip_network(value.strip())
        for value in os.getenv("SMARTGRADE_TRUSTED_PROXY_CIDRS", "").split(",")
        if value.strip()
    ]
    try:
        address = ipaddress.ip_address(peer)
        trusted = any(address in network for network in networks)
        if trusted:
            return str(ipaddress.ip_address(headers.get("x-smartgrade-client-ip", peer)))
    except ValueError:
        pass
    return peer
