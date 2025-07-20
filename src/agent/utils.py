import hashlib
import socket

import psutil


def get_machine_agent_id():
    """Generate a deterministic agent_id for this machine based on MAC and hostname."""

    def get_mac():
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == psutil.AF_LINK and not iface.startswith("lo"):
                    return addr.address
        return "unknown"

    mac = get_mac()
    hostname = socket.gethostname()
    unique_str = f"{mac}-{hostname}"
    return hashlib.sha256(unique_str.encode()).hexdigest()[:16]
