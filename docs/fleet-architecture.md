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

A mobile app (Flutter or similar) connects to a single gateway node over HTTPS:

1. **Discover** — `GET /api/fleet/nodes` returns every node and its VMs.
2. **Select** — user picks a VM from the combined list.
3. **Execute** — `POST /api/fleet/task` sends the task; the gateway routes it.
4. **Stream** — `WS /ws/task/{id}` streams screenshots and action events in real time.

The mobile app never talks to agent nodes directly — the gateway handles all routing.

## GUI Integration

The GUI includes a **Fleet Nodes** tree view panel. Click **Refresh Fleet** to discover peers. Select a remote VM in the tree, enter a task, and press **Start** — the GUI sends the task via HTTP and polls for status updates in the action log.

## Security

- Each node has its own `auth_token`. Peers store the token of the node they connect to.
- No token configured = dev mode (all requests allowed).
- For production, run behind a reverse proxy with TLS or use an SSH tunnel.
