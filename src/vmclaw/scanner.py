"""Network scanner -- discover vmClaw instances on the local subnet."""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

FLEET_PORT = 8077
CONNECT_TIMEOUT = 0.3  # seconds per TCP probe
HTTP_TIMEOUT = 2.0  # seconds for /api/info


@dataclass
class DiscoveredNode:
    """A vmClaw instance found on the network."""

    ip: str
    port: int
    node_name: str
    role: str
    version: str
    vm_count: int = 0


def get_local_ip() -> str:
    """Get this machine's LAN IP address by opening a UDP socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def get_subnet_ips(local_ip: str) -> list[str]:
    """Derive all /24 host addresses from the local IP, excluding self."""
    try:
        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        return [str(ip) for ip in network.hosts() if str(ip) != local_ip]
    except ValueError:
        return []


def probe_port(
    ip: str, port: int = FLEET_PORT, timeout: float = CONNECT_TIMEOUT
) -> bool:
    """Check if a TCP port is open on the given IP."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def query_node_info(
    ip: str, port: int = FLEET_PORT, timeout: float = HTTP_TIMEOUT
) -> Optional[DiscoveredNode]:
    """Hit /api/info on a responsive host and return node metadata."""
    url = f"http://{ip}:{port}"
    try:
        with httpx.Client(
            base_url=url, timeout=timeout, verify=False
        ) as client:
            resp = client.get(
                "/api/info", headers={"Authorization": "Bearer none"}
            )
            resp.raise_for_status()
            d = resp.json()
            return DiscoveredNode(
                ip=ip,
                port=port,
                node_name=d.get("node_name", ""),
                role=d.get("role", "?"),
                version=d.get("version", "?"),
                vm_count=d.get("vm_count", 0),
            )
    except Exception:
        return None


def scan_subnet(
    port: int = FLEET_PORT,
    max_workers: int = 50,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[DiscoveredNode]:
    """Scan the local /24 subnet for vmClaw instances.

    Args:
        port: The fleet server port to probe.
        max_workers: Max parallel threads for port probing.
        on_progress: Optional callback(scanned, total).

    Returns:
        List of DiscoveredNode for each responsive vmClaw instance.
    """
    local_ip = get_local_ip()
    targets = get_subnet_ips(local_ip)
    total = len(targets)
    responsive: list[str] = []

    # Phase 1: Fast TCP port scan
    scanned = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(probe_port, ip, port): ip for ip in targets}
        for future in as_completed(futures):
            scanned += 1
            if on_progress:
                on_progress(scanned, total)
            ip = futures[future]
            try:
                if future.result():
                    responsive.append(ip)
            except Exception:
                pass

    # Phase 2: Query /api/info on responsive hosts
    nodes: list[DiscoveredNode] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(query_node_info, ip, port): ip for ip in responsive
        }
        for future in as_completed(futures):
            try:
                node = future.result()
                if node is not None:
                    nodes.append(node)
            except Exception:
                pass

    return nodes
