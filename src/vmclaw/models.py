"""Data models for vmClaw."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ActionType(Enum):
    CLICK = "click"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    WAIT = "wait"
    DONE = "done"


@dataclass
class Action:
    action: ActionType
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    key: Optional[str] = None
    direction: Optional[str] = None  # "up" or "down"
    reason: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Action:
        raw_x = data.get("x")
        raw_y = data.get("y")
        return cls(
            action=ActionType(data["action"]),
            x=int(raw_x) if raw_x is not None else None,
            y=int(raw_y) if raw_y is not None else None,
            text=data.get("text"),
            key=data.get("key"),
            direction=data.get("direction"),
            reason=data.get("reason", ""),
        )

    def to_dict(self) -> dict:
        """Convert to a JSON-serializable dictionary."""
        d: dict = {"action": self.action.value, "reason": self.reason}
        if self.x is not None:
            d["x"] = self.x
        if self.y is not None:
            d["y"] = self.y
        if self.text is not None:
            d["text"] = self.text
        if self.key is not None:
            d["key"] = self.key
        if self.direction is not None:
            d["direction"] = self.direction
        return d


@dataclass
class VMWindow:
    hwnd: int
    title: str
    pid: int = 0

    def __str__(self) -> str:
        return self.title


@dataclass
class Config:
    provider: str = "openai"  # "openai" or "github"
    openai_api_key: str = ""
    github_token: str = ""
    api_base_url: str = ""  # Optional override for any provider
    model: str = "gpt-4o"
    max_actions: int = 50
    action_delay: float = 1.0
    screenshot_width: int = 1024
    memory_enabled: bool = True
    window_keywords: list[str] = field(
        default_factory=lambda: [
            "vmconnect", "vmware", "virtualbox", "qemu", "hyper-v",
            "virtual machine connection",
        ]
    )
