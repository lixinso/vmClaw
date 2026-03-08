"""vmClaw GUI - tkinter-based graphical interface for the VM agent."""

from __future__ import annotations

import ctypes
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from typing import Any

from PIL import Image, ImageDraw, ImageTk

from .config import load_config
from .discovery import find_vm_windows, find_all_windows
from .models import Action, ActionType, Config, VMWindow
from .orchestrator import run_task

# Import PROVIDERS from main (provider/model registry)
from .main import PROVIDERS, _gh_get_existing_token


class VmClawGui:
    """Main GUI application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("vmClaw - AI VM Agent")
        self.root.geometry("1150x780")
        self.root.minsize(900, 600)

        # State
        self.config: Config = load_config()
        self.event_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.agent_thread: threading.Thread | None = None
        self.vm_windows: list[VMWindow] = []
        self.latest_photo: ImageTk.PhotoImage | None = None
        self._raw_screenshot: Image.Image | None = None
        self.is_running = False
        self._voice_recording = False

        # Fleet state: when a remote VM is selected, this holds the target info
        # {node_name: str, vm_title: str, peer: PeerConfig} or None for local
        self._fleet_target: dict | None = None
        self._fleet_visible: bool = True

        self._build_ui()
        self._populate_providers()
        self._refresh_vm_windows()
        self._poll_queue()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Admin warning banner
        if not self._is_admin():
            banner = tk.Frame(self.root, bg="#FFF3CD")
            tk.Label(
                banner,
                text=(
                    "  Not running as Administrator. "
                    "Input injection into Hyper-V VMs may fail."
                ),
                bg="#FFF3CD",
                fg="#856404",
                anchor="w",
            ).pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
            tk.Button(
                banner, text="Restart as Admin", command=self._restart_as_admin,
            ).pack(side=tk.RIGHT, padx=10, pady=3)
            banner.pack(fill=tk.X)

        # Bottom log panel (pack first so it doesn't get squeezed)
        log_frame = ttk.LabelFrame(self.root, text="Action Log")
        log_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        log_frame.configure(height=180)
        log_frame.pack_propagate(False)
        self._build_log_panel(log_frame)

        # Fleet sidebar (collapsible, far left)
        self._fleet_sidebar = ttk.LabelFrame(self.root, text="Fleet Nodes")
        self._fleet_sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(5, 0), pady=5)
        self._fleet_sidebar.configure(width=220)
        self._fleet_sidebar.pack_propagate(False)
        self._build_fleet_nav(self._fleet_sidebar)

        # Thin opener strip (shown when sidebar is hidden)
        self._fleet_opener = ttk.Frame(self.root, width=24)
        self._fleet_open_btn = ttk.Button(
            self._fleet_opener, text="\u25b6", width=2,
            command=self._toggle_fleet_nav,
        )
        self._fleet_open_btn.pack(side=tk.TOP, pady=(8, 0))
        # Not packed initially — only shown when sidebar is collapsed

        # Left config panel (fixed width)
        self._left_frame = ttk.LabelFrame(self.root, text="Configuration")
        self._left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        self._left_frame.configure(width=280)
        self._left_frame.pack_propagate(False)
        self._build_left_panel(self._left_frame)

        # Right screenshot panel (fills remaining space)
        right_frame = ttk.LabelFrame(self.root, text="Screenshot")
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5), pady=5)
        self._build_right_panel(right_frame)

    def _build_left_panel(self, parent: ttk.LabelFrame) -> None:
        pad = {"padx": 10, "pady": 3, "sticky": "ew"}

        row = 0

        # Provider
        ttk.Label(parent, text="Provider:").grid(row=row, column=0, **pad); row += 1
        self.provider_var = tk.StringVar()
        self.provider_combo = ttk.Combobox(
            parent, textvariable=self.provider_var, state="readonly", width=28,
        )
        self.provider_combo.grid(row=row, column=0, **pad); row += 1
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)

        # Model
        ttk.Label(parent, text="Model:").grid(row=row, column=0, **pad); row += 1
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            parent, textvariable=self.model_var, width=28,
        )
        self.model_combo.grid(row=row, column=0, **pad); row += 1

        # VM Window (local)
        ttk.Label(parent, text="VM Window:").grid(row=row, column=0, **pad); row += 1
        self.vm_var = tk.StringVar()
        self.vm_combo = ttk.Combobox(
            parent, textvariable=self.vm_var, state="readonly", width=28,
        )
        self.vm_combo.grid(row=row, column=0, **pad); row += 1
        self.vm_combo.bind("<<ComboboxSelected>>", self._on_local_vm_selected)
        ttk.Button(
            parent, text="Refresh Windows", command=self._refresh_vm_windows,
        ).grid(row=row, column=0, **pad); row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, pady=4, sticky="ew",
        ); row += 1

        # Max actions + delay + memory (compact row)
        settings_frame = ttk.Frame(parent)
        settings_frame.grid(row=row, column=0, padx=10, pady=2, sticky="ew"); row += 1
        ttk.Label(settings_frame, text="Max:").pack(side=tk.LEFT)
        self.max_actions_var = tk.IntVar(value=self.config.max_actions)
        ttk.Spinbox(
            settings_frame, from_=1, to=200, textvariable=self.max_actions_var, width=5,
        ).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(settings_frame, text="Delay:").pack(side=tk.LEFT)
        self.delay_var = tk.DoubleVar(value=self.config.action_delay)
        ttk.Spinbox(
            settings_frame, from_=0.1, to=10.0, increment=0.1,
            textvariable=self.delay_var, width=5,
        ).pack(side=tk.LEFT, padx=2)

        self.memory_var = tk.BooleanVar(value=self.config.memory_enabled)
        ttk.Checkbutton(
            parent, text="Enable AI Memory", variable=self.memory_var,
        ).grid(row=row, column=0, **pad); row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, pady=4, sticky="ew",
        ); row += 1

        # Target indicator (always visible)
        self.target_var = tk.StringVar(value="Target: local")
        ttk.Label(
            parent, textvariable=self.target_var, foreground="#006699",
            font=("Segoe UI", 8),
        ).grid(row=row, column=0, **pad); row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, pady=4, sticky="ew",
        ); row += 1

        # Task input
        ttk.Label(parent, text="Task:").grid(row=row, column=0, **pad); row += 1
        self.task_text = tk.Text(parent, height=3, width=30, wrap=tk.WORD)
        self.task_text.grid(row=row, column=0, padx=10, pady=3, sticky="ew"); row += 1

        # Bind Enter key to start (Shift+Enter for newline)
        self.task_text.bind("<Return>", self._on_task_enter)

        # Start / Stop buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=0, pady=6); row += 1
        self.start_btn = ttk.Button(
            btn_frame, text="Start", command=self._on_start,
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(
            btn_frame, text="Stop", command=self._on_stop, state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.voice_btn = ttk.Button(
            btn_frame, text="Voice", command=self._on_voice,
        )
        self.voice_btn.pack(side=tk.LEFT, padx=5)

        # Status
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(
            parent, textvariable=self.status_var, foreground="gray",
        ).grid(row=row, column=0, **pad); row += 1

        parent.columnconfigure(0, weight=1)

    def _build_right_panel(self, parent: ttk.LabelFrame) -> None:
        self.screenshot_label = ttk.Label(
            parent, text="No screenshot yet", anchor=tk.CENTER,
        )
        self.screenshot_label.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.screenshot_label.bind("<Configure>", self._on_screenshot_resize)

    def _build_log_panel(self, parent: ttk.LabelFrame) -> None:
        self.log_text = scrolledtext.ScrolledText(
            parent, height=8, state=tk.DISABLED, wrap=tk.WORD,
            font=("Consolas", 9),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_fleet_nav(self, parent: ttk.LabelFrame) -> None:
        """Build the fleet navigation sidebar content."""
        # Close button at top-right
        close_frame = ttk.Frame(parent)
        close_frame.pack(fill=tk.X, padx=5, pady=(2, 0))
        ttk.Button(
            close_frame, text="\u2716", width=3,
            command=self._toggle_fleet_nav,
        ).pack(side=tk.RIGHT)

        # Tree view for nodes and VMs
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(2, 2))

        self.fleet_tree = ttk.Treeview(tree_frame, height=8, selectmode="browse")
        self.fleet_tree.heading("#0", text="Node / VM", anchor="w")
        self.fleet_tree.column("#0", width=190)
        tree_scroll = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self.fleet_tree.yview,
        )
        self.fleet_tree.configure(yscrollcommand=tree_scroll.set)
        self.fleet_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.fleet_tree.bind("<<TreeviewSelect>>", self._on_fleet_select)

        # Refresh button
        ttk.Button(
            parent, text="Refresh Fleet", command=self._refresh_fleet,
        ).pack(padx=5, pady=(2, 5), fill=tk.X)

    def _toggle_fleet_nav(self) -> None:
        """Show or hide the fleet navigation sidebar."""
        if self._fleet_visible:
            self._fleet_sidebar.pack_forget()
            self._fleet_opener.pack(
                side=tk.LEFT, fill=tk.Y, padx=(2, 0), pady=5,
                before=self._left_frame,
            )
        else:
            self._fleet_opener.pack_forget()
            self._fleet_sidebar.pack(
                side=tk.LEFT, fill=tk.Y, padx=(5, 0), pady=5,
                before=self._left_frame,
            )
        self._fleet_visible = not self._fleet_visible

    # ------------------------------------------------------------------
    # Provider / Model population
    # ------------------------------------------------------------------

    def _populate_providers(self) -> None:
        self._provider_id_map: dict[str, str] = {}
        names: list[str] = []
        for pid, info in PROVIDERS.items():
            display = info["name"]
            names.append(display)
            self._provider_id_map[display] = pid

        self.provider_combo["values"] = names

        # Select current config provider
        for display, pid in self._provider_id_map.items():
            if pid == self.config.provider:
                self.provider_var.set(display)
                break
        self._populate_models()

    def _on_provider_changed(self, _event: Any = None) -> None:
        self._populate_models()

    def _populate_models(self) -> None:
        display = self.provider_var.get()
        pid = self._provider_id_map.get(display, "openai")
        models = PROVIDERS[pid]["models"]
        self.model_combo["values"] = models
        if self.config.model in models:
            self.model_var.set(self.config.model)
        elif models:
            self.model_var.set(models[0])

    # ------------------------------------------------------------------
    # VM window refresh
    # ------------------------------------------------------------------

    def _refresh_vm_windows(self) -> None:
        self.vm_windows = find_vm_windows(self.config.window_keywords)
        if not self.vm_windows:
            self.vm_windows = find_all_windows()

        titles = [w.title for w in self.vm_windows]
        self.vm_combo["values"] = titles
        if titles:
            self.vm_combo.current(0)
        self._append_log(f"Found {len(self.vm_windows)} window(s).")

    def _on_local_vm_selected(self, _event: Any = None) -> None:
        """When a local VM is selected in the combo, clear fleet target."""
        self._fleet_target = None
        self.target_var.set("Target: local")
        # Deselect fleet tree
        for sel in self.fleet_tree.selection():
            self.fleet_tree.selection_remove(sel)

    # ------------------------------------------------------------------
    # Fleet discovery and selection
    # ------------------------------------------------------------------

    def _refresh_fleet(self) -> None:
        """Discover fleet peers in a background thread."""
        self._append_log("Fleet: discovering peers...")
        thread = threading.Thread(target=self._fleet_discover_worker, daemon=True)
        thread.start()

    def _fleet_discover_worker(self) -> None:
        """Background thread: query all fleet peers."""
        try:
            from .fleet import FleetClient
            config = load_config()
            fleet_cfg = config.fleet

            if not fleet_cfg.peers:
                self.event_queue.put(("_fleet_result", {"nodes": [], "msg": "No peers configured"}))
                return

            client = FleetClient(fleet_cfg)
            nodes = []

            # Add local node
            local_vms = find_vm_windows(config.window_keywords)
            nodes.append({
                "name": fleet_cfg.node_name or "(local)",
                "role": fleet_cfg.role,
                "reachable": True,
                "local": True,
                "vms": [{"title": vm.title} for vm in local_vms],
            })

            # Query remote peers
            for peer in fleet_cfg.peers:
                info = client.get_info(peer)
                vms = client.list_vms(peer) if info else []
                nodes.append({
                    "name": peer.name,
                    "role": info.role if info else "?",
                    "reachable": info is not None,
                    "local": False,
                    "vms": vms,
                    "peer": peer,
                })

            self.event_queue.put(("_fleet_result", {"nodes": nodes, "msg": ""}))

        except Exception as e:
            self.event_queue.put(("_fleet_result", {"nodes": [], "msg": str(e)}))

    def _populate_fleet_tree(self, data: dict) -> None:
        """Populate the fleet tree view with discovered nodes."""
        # Clear existing items
        for item in self.fleet_tree.get_children():
            self.fleet_tree.delete(item)

        nodes = data.get("nodes", [])
        msg = data.get("msg", "")

        if msg and not nodes:
            self._append_log(f"Fleet: {msg}")
            return

        for node in nodes:
            name = node["name"]
            role = node.get("role", "?")
            is_local = node.get("local", False)
            reachable = node.get("reachable", False)

            if is_local:
                tag = "local"
                label = f"{name} (local, {role})"
            elif reachable:
                tag = "online"
                label = f"{name} ({role})"
            else:
                tag = "offline"
                label = f"{name} (OFFLINE)"

            node_id = self.fleet_tree.insert("", tk.END, text=label, tags=(tag,))

            # Store peer reference in the tree item
            if not is_local and node.get("peer"):
                # Store as item data via a mapping
                if not hasattr(self, "_fleet_peer_map"):
                    self._fleet_peer_map = {}
                self._fleet_peer_map[node_id] = node["peer"]

            # Add VM children
            for vm in node.get("vms", []):
                title = vm if isinstance(vm, str) else vm.get("title", "?")
                vm_tag = "local_vm" if is_local else "remote_vm"
                self.fleet_tree.insert(
                    node_id, tk.END, text=f"VM: {title}",
                    tags=(vm_tag,),
                )

            # Expand node by default
            self.fleet_tree.item(node_id, open=True)

        # Style tags
        self.fleet_tree.tag_configure("offline", foreground="gray")
        self.fleet_tree.tag_configure("online", foreground="#006600")
        self.fleet_tree.tag_configure("local", foreground="#333333")

        count = len(nodes)
        online = sum(1 for n in nodes if n.get("reachable"))
        self._append_log(f"Fleet: {count} node(s), {online} online.")

    def _on_fleet_select(self, _event: Any = None) -> None:
        """Handle selection in the fleet tree view."""
        selection = self.fleet_tree.selection()
        if not selection:
            return

        item = selection[0]
        tags = self.fleet_tree.item(item, "tags")
        text = self.fleet_tree.item(item, "text")

        if "remote_vm" in tags:
            # A remote VM was selected — set fleet target
            parent_id = self.fleet_tree.parent(item)
            peer_map = getattr(self, "_fleet_peer_map", {})
            peer = peer_map.get(parent_id)
            parent_text = self.fleet_tree.item(parent_id, "text")

            # Extract node name from parent text (e.g., "machine-b (agent)")
            node_name = parent_text.split(" (")[0] if " (" in parent_text else parent_text

            # Extract VM title from "VM: Title"
            vm_title = text[4:] if text.startswith("VM: ") else text

            if peer:
                self._fleet_target = {
                    "node_name": node_name,
                    "vm_title": vm_title,
                    "peer": peer,
                }
                self.target_var.set(f"Target: {node_name} / {vm_title}")
            else:
                self._fleet_target = None
                self.target_var.set("Target: local")

        elif "local_vm" in tags:
            # A local VM was selected — find it in the combo
            vm_title = text[4:] if text.startswith("VM: ") else text
            self._fleet_target = None
            self.target_var.set("Target: local")
            # Try to select it in the local VM combo
            for i, w in enumerate(self.vm_windows):
                if w.title == vm_title:
                    self.vm_combo.current(i)
                    break

        else:
            # A node header was selected — no action
            pass

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def _build_config_from_ui(self) -> Config:
        """Read UI values into a Config object."""
        config = load_config()
        display = self.provider_var.get()
        config.provider = self._provider_id_map.get(display, "openai")
        config.model = self.model_var.get()
        config.max_actions = self.max_actions_var.get()
        config.action_delay = self.delay_var.get()
        config.memory_enabled = self.memory_var.get()

        # Auto-fetch GitHub token from gh CLI if not already set
        if config.provider == "github" and not config.github_token:
            token = _gh_get_existing_token()
            if token:
                config.github_token = token

        return config

    def _on_task_enter(self, event: Any) -> str:
        """Handle Enter in task text box — start task (Shift+Enter inserts newline)."""
        if event.state & 0x1:  # Shift held
            return ""  # allow default newline insertion
        self._on_start()
        return "break"  # prevent newline insertion

    def _on_start(self) -> None:
        task = self.task_text.get("1.0", tk.END).strip()
        if not task:
            messagebox.showwarning("No Task", "Please enter a task description.")
            return

        config = self._build_config_from_ui()

        # Check if this is a remote fleet task
        if self._fleet_target is not None:
            self._start_fleet_task(task, config)
            return

        # Local task — need a local VM selected
        idx = self.vm_combo.current()
        if idx < 0 or idx >= len(self.vm_windows):
            messagebox.showwarning("No VM", "Please select a VM window.")
            return

        vm = self.vm_windows[idx]

        # Validate credentials
        if config.provider == "openai" and not config.openai_api_key:
            messagebox.showerror(
                "No API Key",
                "No OpenAI API key configured.\n"
                "Set OPENAI_API_KEY or edit config.toml.",
            )
            return
        if config.provider == "github" and not config.github_token:
            messagebox.showerror(
                "No Token",
                "No GitHub token configured.\n"
                "Set GITHUB_TOKEN or run: gh auth login",
            )
            return

        # Clear log
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

        self.stop_event.clear()
        self.is_running = True
        self._set_controls_enabled(False)
        self.status_var.set("Starting...")

        self.agent_thread = threading.Thread(
            target=self._agent_worker,
            args=(vm, task, config),
            daemon=True,
        )
        self.agent_thread.start()

    def _start_fleet_task(self, task: str, config: Config) -> None:
        """Start a task on a remote fleet node."""
        from .fleet_models import TaskRequest

        target = self._fleet_target
        if target is None:
            return

        # Clear log
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

        self.is_running = True
        self._set_controls_enabled(False)
        self.status_var.set(f"Sending to {target['node_name']}...")

        task_req = TaskRequest(
            vm_title=target["vm_title"],
            task=task,
            max_actions=self.max_actions_var.get(),
            action_delay=self.delay_var.get(),
        )

        self.agent_thread = threading.Thread(
            target=self._fleet_task_worker,
            args=(target, task_req),
            daemon=True,
        )
        self.agent_thread.start()

    def _on_stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping...")
        self.stop_btn.configure(state=tk.DISABLED)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable config controls during a run."""
        state = tk.NORMAL if enabled else tk.DISABLED
        readonly_state = "readonly" if enabled else tk.DISABLED
        self.provider_combo.configure(state=readonly_state)
        self.model_combo.configure(state=state)
        self.vm_combo.configure(state=readonly_state)
        self.task_text.configure(state=state)
        self.start_btn.configure(state=state)
        self.stop_btn.configure(state=tk.NORMAL if not enabled else tk.DISABLED)
        self.voice_btn.configure(state=state)

    # ------------------------------------------------------------------
    # Agent worker thread
    # ------------------------------------------------------------------

    def _agent_worker(self, vm: VMWindow, task: str, config: Config) -> None:
        """Run the agent loop in a background thread."""
        memory = None
        if config.memory_enabled:
            try:
                from .memory import MemoryStore

                memory = MemoryStore()
                memory.open(config)
                self.event_queue.put(("log", "Memory: enabled"))
            except Exception as e:
                self.event_queue.put(("log", f"Memory: disabled ({e})"))
                memory = None

        def on_event(event_type: str, data: Any) -> None:
            self.event_queue.put((event_type, data))

        try:
            run_task(
                vm,
                task,
                config,
                memory=memory,
                on_event=on_event,
                stop_event=self.stop_event,
            )
        except Exception as e:
            self.event_queue.put(("log", f"Error: {e}"))
            self.event_queue.put(("done", "error"))
        finally:
            if memory:
                memory.close()
            self.event_queue.put(("_finished", None))

    def _fleet_task_worker(self, target: dict, task_req: Any) -> None:
        """Send a task to a remote fleet node and poll for status."""
        import time
        from .fleet import FleetClient

        peer = target["peer"]
        node_name = target["node_name"]

        try:
            client = FleetClient(self.config.fleet)
            self.event_queue.put(("log", f"Fleet: sending task to [{node_name}]..."))
            self.event_queue.put(("log", f"  VM: {task_req.vm_title}"))
            self.event_queue.put(("log", f"  Task: {task_req.task}"))

            result = client.submit_task(peer, task_req)
            if result is None or "error" in result:
                err = result.get("error", "unreachable") if result else "unreachable"
                self.event_queue.put(("log", f"Fleet: task failed — {err}"))
                self.event_queue.put(("done", "error"))
                return

            task_id = result.get("task_id", "?")
            self.event_queue.put(("log", f"Fleet: task submitted (id={task_id})"))
            self.event_queue.put(("log", "Fleet: polling for status..."))

            # Poll status until done
            while True:
                if self.stop_event.is_set():
                    # Cancel remote task
                    client.cancel_task(peer, task_id)
                    self.event_queue.put(("log", "Fleet: task cancelled."))
                    self.event_queue.put(("done", "stopped"))
                    break

                time.sleep(2)
                status = client.get_task_status(peer, task_id)
                if status is None:
                    self.event_queue.put(("log", "Fleet: lost connection to node."))
                    self.event_queue.put(("done", "error"))
                    break

                self.event_queue.put((
                    "log",
                    f"  [{status.status}] actions={status.actions_taken}"
                    + (f" outcome={status.outcome}" if status.outcome else ""),
                ))
                self.event_queue.put(("step", status.actions_taken))

                if status.status in ("done", "error", "stopped", "max_actions"):
                    self.event_queue.put(("done", status.status))
                    break

        except Exception as e:
            self.event_queue.put(("log", f"Fleet error: {e}"))
            self.event_queue.put(("done", "error"))
        finally:
            self.event_queue.put(("_finished", None))

    # ------------------------------------------------------------------
    # Queue polling — bridge agent thread events to tkinter main thread
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        """Process pending events from the agent thread."""
        while True:
            try:
                event_type, data = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(str(data))
            elif event_type == "screenshot":
                self._update_screenshot(data)
            elif event_type == "action":
                self._handle_action(data)
            elif event_type == "step":
                self.status_var.set(f"Step {data}...")
            elif event_type == "done":
                self.status_var.set(f"Finished ({data})")
            elif event_type == "_finished":
                self._on_agent_finished()
            elif event_type == "_voice_result":
                self.task_text.insert(tk.END, str(data))
            elif event_type == "_voice_error":
                self._append_log(f"Voice: {data}")
            elif event_type == "_voice_done":
                self._voice_recording = False
                self.voice_btn.configure(text="Voice")
            elif event_type == "_fleet_result":
                self._populate_fleet_tree(data)

        self.root.after(100, self._poll_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _on_agent_finished(self) -> None:
        self.is_running = False
        self._set_controls_enabled(True)

    # ------------------------------------------------------------------
    # Screenshot display
    # ------------------------------------------------------------------

    def _update_screenshot(self, img: Image.Image) -> None:
        self._raw_screenshot = img
        self._render_screenshot(img)

    def _render_screenshot(
        self,
        img: Image.Image,
        click_x: int | None = None,
        click_y: int | None = None,
    ) -> None:
        if img is None:
            return

        # Draw click overlay
        if click_x is not None and click_y is not None:
            img = img.copy()
            draw = ImageDraw.Draw(img)
            r = 12
            draw.ellipse(
                [click_x - r, click_y - r, click_x + r, click_y + r],
                outline="red", width=3,
            )
            draw.line(
                [click_x - r, click_y, click_x + r, click_y],
                fill="red", width=2,
            )
            draw.line(
                [click_x, click_y - r, click_x, click_y + r],
                fill="red", width=2,
            )

        # Scale to fit the label
        label_w = self.screenshot_label.winfo_width()
        label_h = self.screenshot_label.winfo_height()
        if label_w < 10 or label_h < 10:
            label_w, label_h = 800, 600

        img_copy = img.copy()
        img_copy.thumbnail((label_w, label_h), Image.LANCZOS)

        self.latest_photo = ImageTk.PhotoImage(img_copy)
        self.screenshot_label.configure(image=self.latest_photo, text="")

    def _on_screenshot_resize(self, _event: Any) -> None:
        if self._raw_screenshot is not None:
            self._render_screenshot(self._raw_screenshot)

    def _handle_action(self, action: Action) -> None:
        desc = self._format_action_short(action)
        self.status_var.set(desc)
        if action.action == ActionType.CLICK and self._raw_screenshot is not None:
            self._render_screenshot(
                self._raw_screenshot, click_x=action.x, click_y=action.y,
            )

    @staticmethod
    def _format_action_short(action: Action) -> str:
        if action.action == ActionType.CLICK:
            return f"Click ({action.x}, {action.y})"
        elif action.action == ActionType.TYPE:
            text = (action.text or "")[:30]
            return f'Type "{text}"'
        elif action.action == ActionType.KEY:
            return f"Key: {action.key}"
        elif action.action == ActionType.SCROLL:
            return f"Scroll {action.direction}"
        elif action.action == ActionType.WAIT:
            return "Waiting..."
        elif action.action == ActionType.DONE:
            return "Task complete"
        return str(action.action.value)

    # ------------------------------------------------------------------
    # Voice input
    # ------------------------------------------------------------------

    def _on_voice(self) -> None:
        """Toggle voice recording on/off."""
        if self._voice_recording:
            # Stop recording early
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            return

        self._voice_recording = True
        self.voice_btn.configure(text="Listening...")
        self._append_log("Voice: recording... (click 'Listening...' to stop)")

        thread = threading.Thread(target=self._voice_worker, daemon=True)
        thread.start()

    def _voice_worker(self) -> None:
        """Record audio and transcribe in a background thread."""
        try:
            import numpy as np
            import sounddevice as sd
            import speech_recognition as sr

            sample_rate = 16000
            max_seconds = 30

            # Record audio
            audio_data = sd.rec(
                int(max_seconds * sample_rate),
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            )
            sd.wait()  # Blocks until done or sd.stop() called

            # Trim trailing zeros (from early stop via sd.stop())
            flat = audio_data.flatten()
            # Find last non-zero sample
            nonzero = np.nonzero(flat)[0]
            if len(nonzero) == 0:
                self.event_queue.put(("_voice_error", "No audio recorded."))
                return
            flat = flat[: nonzero[-1] + 1]

            if len(flat) < sample_rate * 0.3:
                self.event_queue.put(("_voice_error", "Recording too short."))
                return

            # Transcribe using Google's free speech recognition
            recognizer = sr.Recognizer()
            audio = sr.AudioData(flat.tobytes(), sample_rate, 2)
            text = recognizer.recognize_google(audio)
            self.event_queue.put(("_voice_result", text))
            self.event_queue.put(("log", f"Voice: \"{text}\""))

        except ImportError as e:
            self.event_queue.put((
                "_voice_error",
                f"Missing dependency: {e}. Run: pip install sounddevice SpeechRecognition",
            ))
        except Exception as e:
            err_name = type(e).__name__
            self.event_queue.put(("_voice_error", f"{err_name}: {e}"))
        finally:
            self.event_queue.put(("_voice_done", None))

    # ------------------------------------------------------------------
    # Admin helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def _restart_as_admin() -> None:
        params = subprocess.list2cmdline(sys.argv)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1,
        )
        sys.exit(0)

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self.is_running:
            self.stop_event.set()
        self.root.destroy()


def launch_gui() -> None:
    """Entry point for the vmClaw GUI."""
    root = tk.Tk()
    VmClawGui(root)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
