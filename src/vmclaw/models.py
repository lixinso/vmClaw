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
        return cls(
            action=ActionType(data["action"]),
            x=data.get("x"),
            y=data.get("y"),
            text=data.get("text"),
            key=data.get("key"),
            direction=data.get("direction"),
            reason=data.get("reason", ""),
        )


@dataclass
class VMWindow:
    hwnd: int
    title: str
    pid: int = 0

    def __str__(self) -> str:
        return self.title


@dataclass
class Config:
    openai_api_key: str = ""
    model: str = "gpt-4o"
    max_actions: int = 50
    action_delay: float = 1.0
    screenshot_width: int = 1024
    window_keywords: list[str] = field(
        default_factory=lambda: [
            "vmconnect", "vmware", "virtualbox", "qemu", "hyper-v",
            "virtual machine connection",
        ]
    )
