"""Fleet API server — exposes local VMs and task execution over HTTP/WebSocket."""

from __future__ import annotations

import asyncio
import base64
import threading
import uuid
from io import BytesIO
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .config import load_config
from .discovery import find_vm_windows
from .fleet_models import FleetConfig, NodeInfo, RemoteVM, TaskRequest, TaskStatus
from .models import Config, VMWindow

# ---------------------------------------------------------------------------
# Module-level state (initialised by ``start_server``)
# ---------------------------------------------------------------------------

_config: Config | None = None
_fleet: FleetConfig | None = None

# Running tasks: task_id -> {thread, stop_event, status, events_queue}
_tasks: dict[str, dict[str, Any]] = {}

_security = HTTPBearer()


def _get_config() -> Config:
    if _config is None:
        raise RuntimeError("Server not initialised — call start_server()")
    return _config


def _get_fleet() -> FleetConfig:
    if _fleet is None:
        raise RuntimeError("Server not initialised — call start_server()")
    return _fleet


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def _verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> str:
    """Validate the Bearer token against this node's auth_token."""
    fleet = _get_fleet()
    if not fleet.auth_token:
        # No auth configured — allow all (dev mode)
        return credentials.credentials
    if credentials.credentials != fleet.auth_token:
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")
    return credentials.credentials


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="vmClaw Fleet Agent", version=__version__)


@app.get("/api/info")
async def get_info(_token: str = Depends(_verify_token)) -> dict:
    """Return node metadata."""
    fleet = _get_fleet()
    config = _get_config()
    vms = find_vm_windows(config.window_keywords)
    info = NodeInfo(
        node_name=fleet.node_name,
        role=fleet.role,
        version=__version__,
        vm_count=len(vms),
    )
    return info.to_dict()


@app.get("/api/vms")
async def list_vms(_token: str = Depends(_verify_token)) -> list[dict]:
    """List locally visible VM windows."""
    config = _get_config()
    vms = find_vm_windows(config.window_keywords)
    return [{"title": vm.title, "hwnd": vm.hwnd} for vm in vms]


@app.get("/api/peers")
async def list_peers(_token: str = Depends(_verify_token)) -> list[dict]:
    """List peers this node can reach (for proxy/relay discovery).

    Returns info about each peer including their VMs, allowing upstream
    nodes to discover transitive paths (A -> B -> C).
    """
    from .fleet import FleetClient

    fleet = _get_fleet()
    config = _get_config()
    client = FleetClient(fleet)

    results = []
    for peer in fleet.peers:
        info = client.get_info(peer)
        vms = client.list_vms(peer) if info else []
        results.append({
            "node_name": peer.name,
            "reachable": info is not None,
            "role": info.role if info else None,
            "version": info.version if info else None,
            "vms": vms,
        })

    return results


@app.post("/api/forward")
async def forward_task(
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Forward a task to a downstream peer (proxy chain).

    The request must include a ``target_node`` field identifying which
    downstream peer should execute the task.
    """
    from .fleet import FleetClient

    target_node = req.pop("target_node", None)
    if not target_node:
        raise HTTPException(status_code=400, detail="Missing target_node field")

    try:
        task_req = TaskRequest.from_dict(req)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Bad request: {e}")

    fleet = _get_fleet()
    client = FleetClient(fleet)

    peer = client.find_peer_for_node(target_node)
    if peer is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown downstream node: {target_node}. "
                   f"Known peers: {[p.name for p in fleet.peers]}",
        )

    result = client.submit_task(peer, task_req)
    if result and "error" not in result:
        return result
    else:
        err = result.get("error", "unknown") if result else "unreachable"
        raise HTTPException(status_code=502, detail=f"Forward failed: {err}")


@app.post("/api/task")
async def submit_task(
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Submit a task for local execution. Returns a task_id."""
    try:
        task_req = TaskRequest.from_dict(req)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Bad request: {e}")

    config = _get_config()

    # Resolve VM by title
    vms = find_vm_windows(config.window_keywords)
    vm = _find_vm_by_title(vms, task_req.vm_title)
    if vm is None:
        raise HTTPException(
            status_code=404,
            detail=f"VM not found: {task_req.vm_title}",
        )

    task_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    event_queue: asyncio.Queue[dict] = asyncio.Queue()

    # Apply task-specific overrides to config
    task_config = Config(
        provider=config.provider,
        openai_api_key=config.openai_api_key,
        github_token=config.github_token,
        api_base_url=config.api_base_url,
        model=config.model,
        max_actions=task_req.max_actions,
        action_delay=task_req.action_delay,
        screenshot_width=config.screenshot_width,
        memory_enabled=config.memory_enabled,
        window_keywords=config.window_keywords,
        fleet=config.fleet,
    )

    status = TaskStatus(task_id=task_id, status="running")
    _tasks[task_id] = {
        "thread": None,
        "stop_event": stop_event,
        "status": status,
        "event_queue": event_queue,
        "loop": asyncio.get_event_loop(),
    }

    # Start the task in a background thread
    t = threading.Thread(
        target=_run_task_thread,
        args=(task_id, vm, task_req.task, task_config, stop_event, event_queue),
        daemon=True,
    )
    t.start()
    _tasks[task_id]["thread"] = t

    return {"task_id": task_id, "status": "running"}


@app.get("/api/task/{task_id}")
async def get_task_status(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Get the status of a task."""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _tasks[task_id]["status"].to_dict()


@app.delete("/api/task/{task_id}")
async def cancel_task(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Cancel a running task."""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    task = _tasks[task_id]
    if task["status"].status == "running":
        task["stop_event"].set()
        task["status"].status = "stopped"
    return task["status"].to_dict()


@app.websocket("/ws/task/{task_id}")
async def ws_task_events(
    websocket: WebSocket,
    task_id: str,
    token: str = Query(default=""),
) -> None:
    """Stream real-time task events over WebSocket.

    Authentication via query parameter: /ws/task/{id}?token=xxx
    """
    fleet = _get_fleet()
    if fleet.auth_token and token != fleet.auth_token:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if task_id not in _tasks:
        await websocket.close(code=4004, reason="Task not found")
        return

    await websocket.accept()
    event_queue: asyncio.Queue[dict] = _tasks[task_id]["event_queue"]

    try:
        while True:
            event = await event_queue.get()
            await websocket.send_json(event)
            if event.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

def _find_vm_by_title(vms: list[VMWindow], title: str) -> VMWindow | None:
    """Find a VM by exact or partial title match."""
    title_lower = title.lower()
    # Exact match first
    for vm in vms:
        if vm.title == title:
            return vm
    # Partial match
    for vm in vms:
        if title_lower in vm.title.lower():
            return vm
    return None


def _run_task_thread(
    task_id: str,
    vm: VMWindow,
    task: str,
    config: Config,
    stop_event: threading.Event,
    event_queue: asyncio.Queue[dict],
) -> None:
    """Run orchestrator.run_task in a background thread, pushing events to the queue."""
    from .orchestrator import run_task

    loop = _tasks[task_id]["loop"]
    status = _tasks[task_id]["status"]

    def on_event(event_type: str, data: Any = None) -> None:
        payload: dict = {"type": event_type}
        if event_type == "screenshot" and data is not None:
            # Serialize PIL Image to base64 PNG
            buf = BytesIO()
            data.save(buf, format="PNG")
            payload["data"] = base64.b64encode(buf.getvalue()).decode("utf-8")
        elif event_type == "action" and data is not None:
            payload["data"] = data.to_dict()
        elif event_type == "done":
            payload["data"] = data  # outcome string
        else:
            payload["data"] = str(data) if data is not None else None

        asyncio.run_coroutine_threadsafe(event_queue.put(payload), loop)

    # Initialize memory if enabled
    memory = None
    if config.memory_enabled:
        try:
            from .memory import MemoryStore
            memory = MemoryStore()
            memory.open(config)
        except Exception:
            memory = None

    try:
        history = run_task(
            vm, task, config,
            memory=memory,
            on_event=on_event,
            stop_event=stop_event,
        )
        status.actions_taken = len(history)
        # Determine outcome from the done event or default
        if history and history[-1].action.value == "done":
            status.status = "done"
            status.outcome = history[-1].reason
        elif stop_event.is_set():
            status.status = "stopped"
        else:
            status.status = "max_actions"
    except Exception as e:
        status.status = "error"
        status.outcome = str(e)
        asyncio.run_coroutine_threadsafe(
            event_queue.put({"type": "done", "data": f"error: {e}"}),
            loop,
        )
    finally:
        if memory:
            memory.close()


# ---------------------------------------------------------------------------
# Server launcher
# ---------------------------------------------------------------------------

def start_server(config: Config, host: str = "0.0.0.0", port: int | None = None) -> None:
    """Start the fleet agent server (blocking)."""
    import uvicorn

    global _config, _fleet
    _config = config
    _fleet = config.fleet

    listen_port = port or config.fleet.listen_port
    node_name = config.fleet.node_name or "unnamed"

    print(f"vmClaw Fleet Agent — node '{node_name}' listening on {host}:{listen_port}")
    print(f"Role: {config.fleet.role} | Auth: {'enabled' if config.fleet.auth_token else 'disabled (dev mode)'}")
    print(f"Peers: {len(config.fleet.peers)}")
    print()

    uvicorn.run(app, host=host, port=listen_port, log_level="info")


# ---------------------------------------------------------------------------
# Non-blocking server for embedding in the GUI
# ---------------------------------------------------------------------------

_server_instance: Any | None = None
_server_thread: threading.Thread | None = None


def start_server_background(
    config: Config, host: str = "0.0.0.0", port: int | None = None,
) -> int:
    """Start the fleet server in a background daemon thread. Returns the port."""
    import uvicorn

    global _config, _fleet, _server_instance, _server_thread
    if _server_instance is not None:
        raise RuntimeError("Server is already running")

    _config = config
    _fleet = config.fleet

    listen_port = port or config.fleet.listen_port

    uv_config = uvicorn.Config(
        app, host=host, port=listen_port, log_level="warning",
    )
    _server_instance = uvicorn.Server(uv_config)

    _server_thread = threading.Thread(target=_server_instance.run, daemon=True)
    _server_thread.start()
    return listen_port


def stop_server_background() -> None:
    """Signal the background server to shut down."""
    global _server_instance, _server_thread
    if _server_instance is not None:
        _server_instance.should_exit = True
        _server_instance = None
        _server_thread = None


def is_server_running() -> bool:
    """Check whether the background server is alive."""
    return _server_instance is not None and _server_instance.started
