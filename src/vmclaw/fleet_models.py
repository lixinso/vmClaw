"""Fleet models — shared data structures for the distributed fleet."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class NodeRole(Enum):
    HUB = "hub"
    AGENT = "agent"
    GATEWAY = "gateway"
    HUB_AGENT = "hub+agent"
    HUB_GATEWAY = "hub+gateway"
    AGENT_GATEWAY = "agent+gateway"
    HUB_AGENT_GATEWAY = "hub+agent+gateway"


@dataclass
class PeerConfig:
    """Configuration for a directly connected peer node."""

    name: str
    url: str
    token: str = ""


@dataclass
class FleetConfig:
    """Fleet-related configuration for this node."""

    enabled: bool = False
    node_name: str = ""
    role: str = "agent"
    listen_port: int = 8077
    auth_token: str = ""
    gateway_enabled: bool = False
    peers: list[PeerConfig] = field(default_factory=list)


@dataclass
class NodeInfo:
    """Information about a fleet node (returned by /api/info)."""

    node_name: str
    role: str
    version: str
    vm_count: int = 0

    def to_dict(self) -> dict:
        return {
            "node_name": self.node_name,
            "role": self.role,
            "version": self.version,
            "vm_count": self.vm_count,
        }


@dataclass
class RemoteVM:
    """A VM on a remote node (no hwnd, identified by title)."""

    node_name: str
    title: str
    via: str = ""  # empty if direct, peer name if proxied

    def to_dict(self) -> dict:
        d = {"node_name": self.node_name, "title": self.title}
        if self.via:
            d["via"] = self.via
        return d


@dataclass
class TaskRequest:
    """Request to execute a task on a node."""

    vm_title: str
    task: str
    max_actions: int = 50
    action_delay: float = 1.0

    def to_dict(self) -> dict:
        return {
            "vm_title": self.vm_title,
            "task": self.task,
            "max_actions": self.max_actions,
            "action_delay": self.action_delay,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskRequest:
        return cls(
            vm_title=data["vm_title"],
            task=data["task"],
            max_actions=data.get("max_actions", 50),
            action_delay=data.get("action_delay", 1.0),
        )


@dataclass
class TaskStatus:
    """Status of a task execution."""

    task_id: str
    status: str  # "running", "done", "error", "stopped", "interrupted", "max_actions"
    actions_taken: int = 0
    outcome: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "task_id": self.task_id,
            "status": self.status,
            "actions_taken": self.actions_taken,
        }
        if self.outcome:
            d["outcome"] = self.outcome
        return d
