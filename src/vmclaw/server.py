"""Fleet API server — exposes local VMs and task execution over HTTP/WebSocket."""

from __future__ import annotations

import asyncio
import base64
import json
import queue
import threading
import uuid
from io import BytesIO
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .config import load_config
from .discovery import find_vm_windows
from .fleet_models import FleetConfig, NodeInfo, RemoteVM, TaskRequest, TaskStatus
from .models import Action, ActionType, Config, VMWindow
from .task_store import TaskStore

# ---------------------------------------------------------------------------
# Module-level state (initialised by ``start_server``)
# ---------------------------------------------------------------------------

_config: Config | None = None
_fleet: FleetConfig | None = None

# Running tasks: task_id -> {thread, stop_event, pause_event, approval_queue,
#                             guidance_queue, status, event_queue, loop,
#                             latest_screenshot}
_tasks: dict[str, dict[str, Any]] = {}

# Persistent task history (initialised in start_server / start_server_background)
_task_store: TaskStore | None = None

_security = HTTPBearer(auto_error=False)


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
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    """Validate the Bearer token against this node's auth_token."""
    fleet = _get_fleet()
    if not fleet.auth_token:
        # No auth configured — allow all (dev mode)
        return credentials.credentials if credentials else ""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing auth token")
    if credentials.credentials != fleet.auth_token:
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")
    return credentials.credentials


def _require_gateway() -> None:
    """Raise 404 if gateway is not enabled on this node."""
    fleet = _get_fleet()
    if not fleet.gateway_enabled:
        raise HTTPException(status_code=404, detail="Gateway not enabled on this node")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="vmClaw Fleet Agent", version=__version__)

# CORS — allow mobile/web clients on different origins
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ---------------------------------------------------------------------------
# Fleet-wide aggregated endpoints (gateway)
# ---------------------------------------------------------------------------


@app.get("/api/fleet/nodes")
async def fleet_list_nodes(_token: str = Depends(_verify_token)) -> list[dict]:
    """List all nodes in the fleet (self + peers) with their VMs."""
    from .fleet import FleetClient

    fleet = _get_fleet()
    config = _get_config()

    nodes = []

    # Local node
    local_vms = find_vm_windows(config.window_keywords)
    nodes.append({
        "node_name": fleet.node_name or "local",
        "role": fleet.role,
        "version": __version__,
        "is_self": True,
        "reachable": True,
        "vms": [{"title": vm.title, "hwnd": vm.hwnd} for vm in local_vms],
    })

    # Remote peers
    client = FleetClient(fleet)
    for peer in fleet.peers:
        info = client.get_info(peer)
        vms = client.list_vms(peer) if info else []
        nodes.append({
            "node_name": peer.name,
            "role": info.role if info else None,
            "version": info.version if info else None,
            "is_self": False,
            "reachable": info is not None,
            "vms": vms,
        })

    return nodes


@app.get("/api/fleet/vms")
async def fleet_list_vms(_token: str = Depends(_verify_token)) -> list[dict]:
    """Flat list of all VMs across the fleet with their node info."""
    from .fleet import FleetClient

    fleet = _get_fleet()
    config = _get_config()

    all_vms = []

    # Local VMs
    local_vms = find_vm_windows(config.window_keywords)
    for vm in local_vms:
        all_vms.append({
            "node_name": fleet.node_name or "local",
            "title": vm.title,
            "is_local": True,
        })

    # Remote VMs
    client = FleetClient(fleet)
    for peer in fleet.peers:
        vms = client.list_vms(peer)
        for vm in vms:
            title = vm if isinstance(vm, str) else vm.get("title", "?")
            all_vms.append({
                "node_name": peer.name,
                "title": title,
                "is_local": False,
            })

    return all_vms


@app.post("/api/fleet/task")
async def fleet_submit_task(
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Submit a task to any node in the fleet (auto-routes).

    Requires ``node_name`` and ``vm_title`` fields. If the target is this
    node, executes locally. Otherwise, forwards to the appropriate peer.
    """
    from .fleet import FleetClient

    target_node = req.pop("node_name", None)
    if not target_node:
        raise HTTPException(status_code=400, detail="Missing node_name field")

    try:
        task_req = TaskRequest.from_dict(req)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Bad request: {e}")

    fleet = _get_fleet()

    # Check if the target is this node
    if target_node == (fleet.node_name or "local"):
        # Execute locally — reuse the local task endpoint logic
        return await submit_task(task_req.to_dict(), _token)

    # Forward to remote peer
    client = FleetClient(fleet)
    peer = client.find_peer_for_node(target_node)
    if peer is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown node: {target_node}",
        )

    result = client.submit_task(peer, task_req)
    if result and "error" not in result:
        result["node_name"] = target_node
        return result
    else:
        err = result.get("error", "unknown") if result else "unreachable"
        raise HTTPException(status_code=502, detail=f"Forward to {target_node} failed: {err}")


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
    pause_event = threading.Event()
    approval_q: queue.Queue = queue.Queue()
    guidance_q: queue.Queue = queue.Queue()
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
        "pause_event": pause_event,
        "approval_queue": approval_q,
        "guidance_queue": guidance_q,
        "status": status,
        "event_queue": event_queue,
        "loop": asyncio.get_event_loop(),
        "latest_screenshot": None,  # bytes (JPEG) cached for mobile
        "vm_title": task_req.vm_title,
        "task_text": task_req.task,
    }

    # Record in persistent task store
    if _task_store is not None:
        fleet = _get_fleet()
        _task_store.create_task(
            task_id=task_id,
            node_name=fleet.node_name or "local",
            vm_title=task_req.vm_title,
            task_text=task_req.task,
        )

    # Start the task in a background thread
    t = threading.Thread(
        target=_run_task_thread,
        args=(task_id, vm, task_req.task, task_config, stop_event,
              event_queue, pause_event, approval_q, guidance_q),
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


@app.post("/api/task/{task_id}/pause")
async def pause_task(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Pause a running task."""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    task = _tasks[task_id]
    if task["status"].status != "running":
        raise HTTPException(status_code=409, detail="Task is not running")
    task["pause_event"].set()
    return {"task_id": task_id, "paused": True}


@app.post("/api/task/{task_id}/resume")
async def resume_task(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Resume a paused task."""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    task = _tasks[task_id]
    task["pause_event"].clear()
    return {"task_id": task_id, "paused": False}


@app.post("/api/task/{task_id}/approve")
async def approve_action(
    task_id: str,
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Approve or reject a pending action."""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    approved = req.get("approved", True)
    _tasks[task_id]["approval_queue"].put(bool(approved))
    return {"task_id": task_id, "approved": approved}


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
    pause_event: threading.Event | None = None,
    approval_queue: queue.Queue | None = None,
    guidance_queue: queue.Queue | None = None,
) -> None:
    """Run orchestrator.run_task in a background thread, pushing events to the queue."""
    from .orchestrator import run_task

    loop = _tasks[task_id]["loop"]
    status = _tasks[task_id]["status"]

    def on_event(event_type: str, data: Any = None) -> None:
        payload: dict = {"type": event_type}
        if event_type == "screenshot" and data is not None:
            # Serialize PIL Image to base64 PNG for WebSocket
            buf = BytesIO()
            data.save(buf, format="PNG")
            payload["data"] = base64.b64encode(buf.getvalue()).decode("utf-8")
            # Cache as JPEG for mobile screenshot endpoint
            jpeg_buf = BytesIO()
            img_for_cache = data
            # Resize to max 720px wide for mobile bandwidth
            if data.width > 720:
                ratio = 720 / data.width
                new_size = (720, int(data.height * ratio))
                img_for_cache = data.resize(new_size)
            img_for_cache.save(jpeg_buf, format="JPEG", quality=70)
            _tasks[task_id]["latest_screenshot"] = jpeg_buf.getvalue()
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
            from .memory import MemoryStore, resolve_vm_id
            vm_id = resolve_vm_id(vm.title, config)
            memory = MemoryStore(vm_id=vm_id)
            memory.open(config)
        except Exception:
            memory = None

    try:
        history = run_task(
            vm, task, config,
            memory=memory,
            on_event=on_event,
            stop_event=stop_event,
            pause_event=pause_event,
            approval_queue=approval_queue,
            guidance_queue=guidance_queue,
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
        # Update persistent task store
        if _task_store is not None:
            actions_json = "[]"
            try:
                actions_json = json.dumps([a.to_dict() for a in history])
            except Exception:
                pass
            _task_store.update_status(
                task_id=task_id,
                status=status.status,
                outcome=status.outcome,
                actions_taken=status.actions_taken,
                actions_json=actions_json,
            )


# ---------------------------------------------------------------------------
# Mobile / Gateway API  (gated by gateway_enabled)
# ---------------------------------------------------------------------------


@app.get("/api/mobile/info")
async def mobile_info(_token: str = Depends(_verify_token)) -> dict:
    """Gateway info + fleet summary for mobile clients."""
    _require_gateway()
    fleet = _get_fleet()
    config = _get_config()
    local_vms = find_vm_windows(config.window_keywords)
    return {
        "node_name": fleet.node_name or "local",
        "role": fleet.role,
        "version": __version__,
        "gateway_enabled": fleet.gateway_enabled,
        "vm_count": len(local_vms),
        "peer_count": len(fleet.peers),
    }


@app.get("/api/mobile/nodes")
async def mobile_list_nodes(_token: str = Depends(_verify_token)) -> list[dict]:
    """List all reachable nodes (local + peers) with status/VM count."""
    _require_gateway()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .fleet import FleetClient

    fleet = _get_fleet()
    config = _get_config()
    nodes = []

    # Local node
    local_vms = find_vm_windows(config.window_keywords)
    running = sum(1 for t in _tasks.values() if t["status"].status == "running")
    nodes.append({
        "node_name": fleet.node_name or "local",
        "role": fleet.role,
        "status": "online",
        "is_self": True,
        "vm_count": len(local_vms),
        "running_tasks": running,
    })

    # Remote peers — query in parallel with short timeout
    client = FleetClient(fleet, timeout=2.0)

    def _probe_peer(peer):
        info = client.get_info(peer)
        vms = client.list_vms(peer) if info else []
        return {
            "node_name": peer.name,
            "role": info.role if info else None,
            "status": "online" if info else "offline",
            "is_self": False,
            "vm_count": len(vms),
            "running_tasks": 0,
        }

    with ThreadPoolExecutor(max_workers=len(fleet.peers) or 1) as pool:
        futures = {pool.submit(_probe_peer, p): p for p in fleet.peers}
        for fut in as_completed(futures):
            try:
                nodes.append(fut.result())
            except Exception:
                peer = futures[fut]
                nodes.append({
                    "node_name": peer.name,
                    "role": None,
                    "status": "offline",
                    "is_self": False,
                    "vm_count": 0,
                    "running_tasks": 0,
                })

    return nodes


@app.get("/api/mobile/nodes/{node_name}/vms")
async def mobile_list_node_vms(
    node_name: str,
    _token: str = Depends(_verify_token),
) -> list[dict]:
    """List VMs on a specific node."""
    _require_gateway()
    from .fleet import FleetClient

    fleet = _get_fleet()
    config = _get_config()

    # Local node
    local_name = fleet.node_name or "local"
    if node_name == local_name:
        vms = find_vm_windows(config.window_keywords)
        return [{"title": vm.title, "hwnd": vm.hwnd} for vm in vms]

    # Remote peer
    client = FleetClient(fleet)
    peer = client.find_peer_for_node(node_name)
    if peer is None:
        raise HTTPException(status_code=404, detail=f"Unknown node: {node_name}")
    return client.list_vms(peer)


@app.get("/api/mobile/tasks")
async def mobile_list_tasks(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _token: str = Depends(_verify_token),
) -> list[dict]:
    """List task history (persistent, survives restart)."""
    _require_gateway()
    if _task_store is None:
        return []
    records = _task_store.list_tasks(status=status, limit=limit, offset=offset)
    return [r.to_dict() for r in records]


@app.get("/api/mobile/tasks/{task_id}")
async def mobile_get_task(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Get task detail — tries in-memory first, then persistent store."""
    _require_gateway()
    # In-memory (running/recent)
    if task_id in _tasks:
        return _tasks[task_id]["status"].to_dict()
    # Persistent store
    if _task_store is not None:
        rec = _task_store.get_task(task_id)
        if rec is not None:
            return rec.to_dict()
    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@app.get("/api/mobile/tasks/{task_id}/screenshot")
async def mobile_get_screenshot(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> Response:
    """Return the latest cached JPEG screenshot for a running task."""
    _require_gateway()
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    jpeg_bytes = _tasks[task_id].get("latest_screenshot")
    if jpeg_bytes is None:
        raise HTTPException(status_code=404, detail="No screenshot available yet")
    return Response(content=jpeg_bytes, media_type="image/jpeg")


@app.post("/api/mobile/tasks")
async def mobile_submit_task(
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Submit a task — auto-routes to local node or forwards to peer."""
    _require_gateway()
    from .fleet import FleetClient

    target_node = req.pop("node_name", None)
    fleet = _get_fleet()
    local_name = fleet.node_name or "local"

    try:
        task_req = TaskRequest.from_dict(req)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Bad request: {e}")

    # Local execution
    if not target_node or target_node == local_name:
        return await submit_task(task_req.to_dict(), _token)

    # Remote execution
    client = FleetClient(fleet)
    peer = client.find_peer_for_node(target_node)
    if peer is None:
        raise HTTPException(status_code=404, detail=f"Unknown node: {target_node}")

    result = client.submit_task(peer, task_req)
    if result and "error" not in result:
        # Record in persistent store as a remote task
        if _task_store is not None:
            remote_id = result.get("task_id", "?")
            _task_store.create_task(
                task_id=remote_id,
                node_name=target_node,
                vm_title=task_req.vm_title,
                task_text=task_req.task,
            )
        result["node_name"] = target_node
        return result
    err = result.get("error", "unknown") if result else "unreachable"
    raise HTTPException(status_code=502, detail=f"Forward to {target_node} failed: {err}")


@app.post("/api/mobile/tasks/{task_id}/cancel")
async def mobile_cancel_task(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Cancel a task (local or remote)."""
    _require_gateway()
    if task_id in _tasks:
        return await cancel_task(task_id, _token)
    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@app.post("/api/mobile/tasks/{task_id}/pause")
async def mobile_pause_task(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Pause a running task."""
    _require_gateway()
    if task_id in _tasks:
        return await pause_task(task_id, _token)
    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@app.post("/api/mobile/tasks/{task_id}/resume")
async def mobile_resume_task(
    task_id: str,
    _token: str = Depends(_verify_token),
) -> dict:
    """Resume a paused task."""
    _require_gateway()
    if task_id in _tasks:
        return await resume_task(task_id, _token)
    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@app.post("/api/mobile/tasks/{task_id}/approve")
async def mobile_approve_action(
    task_id: str,
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Approve or reject a pending action."""
    _require_gateway()
    if task_id in _tasks:
        return await approve_action(task_id, req, _token)
    raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")


@app.post("/api/mobile/tasks/{task_id}/guide-click")
async def mobile_guide_click(
    task_id: str,
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Inject a click action into a running task."""
    _require_gateway()
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    x = req.get("x")
    y = req.get("y")
    if x is None or y is None:
        raise HTTPException(status_code=400, detail="Missing x or y")
    action = Action(action=ActionType.CLICK, x=int(x), y=int(y), reason="Mobile user guidance")
    _tasks[task_id]["guidance_queue"].put(action)
    return {"task_id": task_id, "injected": "click", "x": x, "y": y}


@app.post("/api/mobile/tasks/{task_id}/guide-type")
async def mobile_guide_type(
    task_id: str,
    req: dict,
    _token: str = Depends(_verify_token),
) -> dict:
    """Inject a type action into a running task."""
    _require_gateway()
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    text = req.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")
    action = Action(action=ActionType.TYPE, text=text, reason="Mobile user guidance")
    _tasks[task_id]["guidance_queue"].put(action)
    return {"task_id": task_id, "injected": "type"}


@app.websocket("/ws/mobile/tasks/{task_id}")
async def ws_mobile_task_events(
    websocket: WebSocket,
    task_id: str,
    token: str = Query(default=""),
) -> None:
    """Stream task events to mobile client.

    For local tasks, reads from the in-memory event queue.
    For remote tasks, relays events from the peer node's WebSocket.
    """
    fleet = _get_fleet()
    if fleet.auth_token and token != fleet.auth_token:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Local task — stream from in-memory queue
    if task_id in _tasks:
        await websocket.accept()
        eq: asyncio.Queue[dict] = _tasks[task_id]["event_queue"]
        try:
            while True:
                event = await eq.get()
                # For mobile WS, convert screenshots to JPEG base64
                if event.get("type") == "screenshot" and _tasks[task_id].get("latest_screenshot"):
                    jpeg_b64 = base64.b64encode(
                        _tasks[task_id]["latest_screenshot"]
                    ).decode("utf-8")
                    await websocket.send_json({"type": "screenshot", "data": jpeg_b64})
                else:
                    await websocket.send_json(event)
                if event.get("type") == "done":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        return

    # Task not found locally
    await websocket.close(code=4004, reason="Task not found")


# ---------------------------------------------------------------------------
# Server launcher
# ---------------------------------------------------------------------------

def start_server(config: Config, host: str = "0.0.0.0", port: int | None = None) -> None:
    """Start the fleet agent server (blocking)."""
    import uvicorn

    global _config, _fleet, _task_store
    _config = config
    _fleet = config.fleet

    _task_store = TaskStore()
    _task_store.open()

    listen_port = port or config.fleet.listen_port
    node_name = config.fleet.node_name or "unnamed"

    print(f"vmClaw Fleet Agent — node '{node_name}' listening on {host}:{listen_port}")
    print(f"Role: {config.fleet.role} | Auth: {'enabled' if config.fleet.auth_token else 'disabled (dev mode)'}")
    print(f"Gateway: {'enabled' if config.fleet.gateway_enabled else 'disabled'}")
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

    global _config, _fleet, _server_instance, _server_thread, _task_store
    if _server_instance is not None:
        raise RuntimeError("Server is already running")

    _config = config
    _fleet = config.fleet

    _task_store = TaskStore()
    _task_store.open()

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
