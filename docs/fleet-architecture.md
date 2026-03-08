# vmClaw Fleet Architecture

## Overview

Fleet mode turns any vmClaw instance into a networked node. Each node exposes a REST + WebSocket API so that other nodes (or the GUI) can discover VMs, submit tasks, and stream live progress. Nodes connect peer-to-peer — there is no central server.

```
  Machine A (hub)              Machine B (agent)
  +-----------------+          +-----------------+
  | vmclaw serve    |  REST /  | vmclaw serve    |
  | port 8077       |--------->| port 8077       |
  | 2 local VMs     |  WS      | 3 local VMs     |
  +-----------------+          +--------+--------+
        |                               |
        |          Machine C (agent)    |
        |          +-----------------+  |
        +--------->| vmclaw serve    |<-+
                   | port 8077       |
                   | 1 local VM      |
                   +-----------------+
```

## Node Roles

| Role | Description |
|------|-------------|
| `hub` | Sends tasks to other nodes. Does not execute locally. |
| `agent` | Executes tasks on local VMs. |
| `hub+agent` | Both sends and executes. |

Roles are informational — any node can call any endpoint on any peer it has configured.

## Configuration

Add a `[fleet]` section to `config.toml`:

```toml
[fleet]
enabled = true
node_name = "office-pc"
role = "hub+agent"
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

## GUI Integration

The GUI includes a **Fleet Nodes** tree view panel. Click **Refresh Fleet** to discover peers. Select a remote VM in the tree, enter a task, and press **Start** — the GUI sends the task via HTTP and polls for status updates in the action log.

## Security

- Each node has its own `auth_token`. Peers store the token of the node they connect to.
- No token configured = dev mode (all requests allowed).
- For production, run behind a reverse proxy with TLS or use an SSH tunnel.
