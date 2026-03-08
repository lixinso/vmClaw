"""Fleet client — discover peers, send tasks, stream events."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable

import httpx

from .fleet_models import FleetConfig, NodeInfo, PeerConfig, RemoteVM, TaskRequest, TaskStatus


class FleetClient:
    """HTTP/WebSocket client for communicating with fleet peers."""

    def __init__(self, config: FleetConfig, timeout: float = 10.0) -> None:
        self.config = config
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _headers(self, peer: PeerConfig) -> dict[str, str]:
        """Build auth headers for a peer."""
        # Always send Authorization header — HTTPBearer on older servers
        # rejects requests without it, even when no auth token is configured.
        return {"Authorization": f"Bearer {peer.token or 'none'}"}

    def _client(self, peer: PeerConfig) -> httpx.Client:
        """Create a sync HTTP client for a peer."""
        return httpx.Client(
            base_url=peer.url,
            headers=self._headers(peer),
            timeout=self.timeout,
            verify=False,  # allow self-signed certs on LAN
        )

    def _async_client(self, peer: PeerConfig) -> httpx.AsyncClient:
        """Create an async HTTP client for a peer."""
        return httpx.AsyncClient(
            base_url=peer.url,
            headers=self._headers(peer),
            timeout=self.timeout,
            verify=False,
        )

    # ------------------------------------------------------------------
    # Sync API (used by CLI)
    # ------------------------------------------------------------------

    def get_info(self, peer: PeerConfig) -> NodeInfo | None:
        """Get node info from a peer. Returns None on error."""
        try:
            with self._client(peer) as client:
                resp = client.get("/api/info")
                resp.raise_for_status()
                d = resp.json()
                return NodeInfo(
                    node_name=d["node_name"],
                    role=d["role"],
                    version=d["version"],
                    vm_count=d.get("vm_count", 0),
                )
        except Exception:
            return None

    def list_vms(self, peer: PeerConfig) -> list[dict]:
        """List VMs on a peer. Returns empty list on error."""
        try:
            with self._client(peer) as client:
                resp = client.get("/api/vms")
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return []

    def list_peers(self, peer: PeerConfig) -> list[dict]:
        """List transitive peers known to a peer. Returns empty list on error."""
        try:
            with self._client(peer) as client:
                resp = client.get("/api/peers")
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return []

    def submit_task(self, peer: PeerConfig, request: TaskRequest) -> dict | None:
        """Submit a task to a peer. Returns response dict or None on error."""
        try:
            with self._client(peer) as client:
                resp = client.post("/api/task", json=request.to_dict())
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def get_task_status(self, peer: PeerConfig, task_id: str) -> TaskStatus | None:
        """Get task status from a peer."""
        try:
            with self._client(peer) as client:
                resp = client.get(f"/api/task/{task_id}")
                resp.raise_for_status()
                d = resp.json()
                return TaskStatus(
                    task_id=d["task_id"],
                    status=d["status"],
                    actions_taken=d.get("actions_taken", 0),
                    outcome=d.get("outcome"),
                )
        except Exception:
            return None

    def cancel_task(self, peer: PeerConfig, task_id: str) -> dict | None:
        """Cancel a task on a peer."""
        try:
            with self._client(peer) as client:
                resp = client.delete(f"/api/task/{task_id}")
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return None

    def forward_task(
        self, peer: PeerConfig, target_node: str, request: TaskRequest
    ) -> dict | None:
        """Forward a task through a peer to a downstream node."""
        try:
            payload = {
                "target_node": target_node,
                **request.to_dict(),
            }
            with self._client(peer) as client:
                resp = client.post("/api/forward", json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Discovery: build a full picture of the fleet
    # ------------------------------------------------------------------

    def discover_all(self) -> dict[str, dict]:
        """Query all direct peers and return a fleet map.

        Returns:
            Dict of node_name -> {info: NodeInfo, vms: list[dict], peer: PeerConfig,
                                   reachable: bool, transitive_peers: list[dict]}
        """
        fleet_map: dict[str, dict] = {}

        for peer in self.config.peers:
            info = self.get_info(peer)
            vms = self.list_vms(peer) if info else []
            transitive = self.list_peers(peer) if info else []

            fleet_map[peer.name] = {
                "info": info,
                "vms": vms,
                "peer": peer,
                "reachable": info is not None,
                "transitive_peers": transitive,
            }

        return fleet_map

    def find_peer_for_node(self, node_name: str) -> PeerConfig | None:
        """Find the direct peer config for a node name."""
        for peer in self.config.peers:
            if peer.name == node_name:
                return peer
        return None

    # ------------------------------------------------------------------
    # WebSocket event streaming
    # ------------------------------------------------------------------

    async def stream_events(
        self,
        peer: PeerConfig,
        task_id: str,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> AsyncIterator[dict]:
        """Stream events from a remote task via WebSocket.

        Yields event dicts: {"type": "screenshot"|"action"|"log"|"done", "data": ...}
        """
        import websockets

        # Build WebSocket URL from peer's HTTP URL
        ws_url = peer.url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/ws/task/{task_id}?token={peer.token}"

        async with websockets.connect(ws_url, ssl=None) as ws:
            async for message in ws:
                event = json.loads(message)
                if on_event:
                    on_event(event.get("type", ""), event.get("data"))
                yield event
                if event.get("type") == "done":
                    break
