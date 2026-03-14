# vmClaw Fleet Architecture

## Overview

Fleet mode turns any vmClaw instance into a networked node. Each node exposes a REST + WebSocket API so that other nodes (or the GUI) can discover VMs, submit tasks, and stream live progress. Nodes connect peer-to-peer — there is no central server.

Every node can act as a **hub** (sends tasks), **agent** (executes tasks), and **gateway** (aggregates the fleet for external clients such as a mobile app). Roles are composable — a single node can be all three at once.

```
  ┌─────────────────┐
  │   Mobile App     │  Flutter / REST
  │  (fleet ctrl)    │
  └────────┬────────┘
           │ HTTPS
           ▼
  ┌──────────────────────────────────────┐
  │  Machine A  (hub + agent + gateway)  │
  │  vmclaw serve  :8077                 │
  │  2 local VMs                         │
  │                                      │
  │  /api/fleet/nodes  — all nodes       │
  │  /api/fleet/vms    — all VMs         │
  │  /api/fleet/task   — auto-route      │
  └──────┬───────────────────┬───────────┘
         │  REST / WS        │  REST / WS
         ▼                   ▼
  ┌─────────────────┐  ┌─────────────────┐
  │  Machine B      │  │  Machine C      │
  │  (agent)        │  │  (agent)        │
  │  3 local VMs    │  │  1 local VM     │
  └─────────────────┘  └─────────────────┘
```

## Node Roles

| Role | Description |
|------|-------------|
| `hub` | Sends tasks to other nodes. Does not execute locally. |
| `agent` | Executes tasks on local VMs. |
| `gateway` | Exposes fleet-wide aggregated API endpoints for external clients (e.g., mobile app). |
| `hub+agent` | Both sends and executes. |
| `hub+agent+gateway` | Full node — sends, executes, and serves external clients. |

Roles are composable with `+` and informational — any node can call any endpoint on any peer it has configured. The `gateway` flag signals that this node is intended as the external entry point for the fleet.

## Gateway API

When a node includes the `gateway` role, it exposes additional fleet-wide endpoints that aggregate data from all peers:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fleet/nodes` | GET | List all nodes (self + peers) with their VMs and status |
| `/api/fleet/vms` | GET | Flat list of every VM across the fleet with node info |
| `/api/fleet/task` | POST | Submit a task to any node/VM — auto-routes to the correct peer |

These endpoints are additive — the original per-node endpoints (`/api/info`, `/api/vms`, `/api/task`) remain unchanged.

### Fleet task request

```json
{
  "node_name": "lab-server",
  "vm_title": "Win11 - VMConnect",
  "task": "Open Notepad and type hello",
  "max_actions": 50,
  "action_delay": 1.0
}
```

If `node_name` matches the gateway node itself, the task executes locally. Otherwise it is forwarded to the appropriate peer.

## Configuration

Add a `[fleet]` section to `config.toml`:

```toml
[fleet]
enabled = true
node_name = "office-pc"
role = "hub+agent+gateway"
listen_port = 8077
auth_token = "my-secret"

[[fleet.peers]]
name = "lab-server"
url = "http://192.168.1.50:8077"
token = "lab-secret"

[[fleet.peers]]
name = "home-pc"
url = "http://10.0.0.5:8077"
token = "home-secret"
```

## CLI Commands

```
vmclaw serve                     # Start the fleet API server
vmclaw serve --port 9000         # Custom port
vmclaw serve --name my-node      # Override node name
vmclaw serve --token secret      # Override auth token

vmclaw fleet list                # Show all nodes and their VMs
vmclaw fleet run \
  --node lab-server \
  --vm "Win11 - VMConnect" \
  --task "Open Notepad" \
  --follow                       # Send task and poll status

vmclaw fleet run \
  --all \
  --vm "Alice" \
  --task "Install update"        # Broadcast to all peers
```

## Proxy Chains

Nodes don't all need direct connectivity. If Machine A can reach Machine B, and Machine B can reach Machine C, then A can forward a task to C through B. When A queries B's peers, it learns that C exists and can route through B automatically.

## Mobile App Integration

A mobile app (Flutter) connects to a single gateway-enabled node over HTTPS/WSS using the same Bearer token as fleet peers.

### Architecture

```
Flutter Mobile App
  │
  │ HTTPS / WSS (same Bearer token as fleet)
  ▼
vmClaw Node (role: hub+agent, gateway_enabled: true)
  ├── executes tasks on local VMs     (agent)
  ├── relays tasks to peer nodes      (hub)
  └── serves mobile API routes        (gateway)
       │
       ├──► vmClaw Node B (agent) ──► local VMs
       └──► vmClaw Node C (agent) ──► local VMs
```

The mobile app is a **remote fleet controller** — it does not run AI, capture screenshots, or execute actions. It connects to one gateway-enabled node and controls the entire fleet through it.

### Mobile API Endpoints

All gated by `gateway_enabled = true` — return 404 when disabled.

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/mobile/info` | Gateway info + fleet summary |
| GET | `/api/mobile/nodes` | All reachable nodes with online/offline status |
| GET | `/api/mobile/nodes/{node}/vms` | VMs on a specific node |
| GET | `/api/mobile/tasks` | Task history (paginated, persists across restarts) |
| GET | `/api/mobile/tasks/{id}` | Task detail + status |
| GET | `/api/mobile/tasks/{id}/screenshot` | Latest screenshot as JPEG |
| POST | `/api/mobile/tasks` | Submit task (auto-routes to local or peer node) |
| POST | `/api/mobile/tasks/{id}/cancel` | Cancel task |
| POST | `/api/mobile/tasks/{id}/pause` | Pause task |
| POST | `/api/mobile/tasks/{id}/resume` | Resume task |
| POST | `/api/mobile/tasks/{id}/approve` | Approve pending action |
| POST | `/api/mobile/tasks/{id}/guide-click` | Inject click at `{x, y}` |
| POST | `/api/mobile/tasks/{id}/guide-type` | Inject typed text |
| WS | `/ws/mobile/tasks/{id}` | Live event stream (JPEG screenshots) |

### Pause / Resume / Approval

These endpoints are also available on the node API (`/api/task/{id}/pause`, `/resume`, `/approve`) for use by any fleet client.

When a task is paused, the orchestrator blocks before the next action. When an approval-required action is detected (TYPE or KEY), the orchestrator emits an `approval_required` event and blocks until approved or rejected via the `/approve` endpoint.

### Guide-Click / Guide-Type

Mobile users can "take over" temporarily by tapping the screenshot to inject a click or typing text. The injected action is consumed by the orchestrator instead of querying the AI model for that step.

### WebSocket Event Format

All WebSocket messages are JSON objects with `type` and `data` fields:

```json
{"type": "step",        "data": 3}
{"type": "screenshot",  "data": "<base64-jpeg>"}
{"type": "action",      "data": {"action": "click", "x": 512, "y": 384, "reason": "..."}}
{"type": "log",         "data": "Capturing screenshot..."}
{"type": "tokens",      "data": {"prompt_tokens": 1200, "completion_tokens": 80}}
{"type": "paused",      "data": "Task paused by user"}
{"type": "resumed",     "data": "Task resumed"}
{"type": "approval_required", "data": {"action": "type", "text": "...", "reason": "..."}}
{"type": "done",        "data": "done"}
```

### Mobile Interaction Modes

| Mode | Description |
|------|-------------|
| **Observe** | Watch task execution — screenshots, actions, logs |
| **Supervise** | Must approve risky actions (TYPE/KEY) before they execute |
| **Intervene** | Tap screenshot to inject clicks/types, overriding the AI |

## GUI Integration

The GUI includes a **Fleet Nodes** tree view panel. Click **Refresh Fleet** to discover peers. Select a remote VM in the tree, enter a task, and press **Start** — the GUI sends the task via HTTP and polls for status updates in the action log.

A **Gateway (Mobile)** checkbox in the fleet sidebar enables mobile access on the same port. When active, the `/api/mobile/*` routes become available.

## Security

- Each node has its own `auth_token`. Peers store the token of the node they connect to.
- Mobile uses the same Bearer token as fleet peers.
- No token configured = dev mode (all requests allowed).
- Mobile screenshots are compressed JPEG (quality 70, max 720px wide) to reduce bandwidth.
- For production, run behind a reverse proxy with TLS or use an SSH tunnel.
