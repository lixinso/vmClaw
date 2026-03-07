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

        # Left config panel (fixed width)
        left_frame = ttk.LabelFrame(self.root, text="Configuration")
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_frame.configure(width=280)
        left_frame.pack_propagate(False)
        self._build_left_panel(left_frame)

        # Right screenshot panel (fills remaining space)
        right_frame = ttk.LabelFrame(self.root, text="Screenshot")
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5), pady=5)
        self._build_right_panel(right_frame)

    def _build_left_panel(self, parent: ttk.LabelFrame) -> None:
        pad = {"padx": 10, "pady": 3, "sticky": "ew"}

        # Provider
        ttk.Label(parent, text="Provider:").grid(row=0, column=0, **pad)
        self.provider_var = tk.StringVar()
        self.provider_combo = ttk.Combobox(
            parent, textvariable=self.provider_var, state="readonly", width=28,
        )
        self.provider_combo.grid(row=1, column=0, **pad)
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)

        # Model
        ttk.Label(parent, text="Model:").grid(row=2, column=0, **pad)
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            parent, textvariable=self.model_var, width=28,
        )
        self.model_combo.grid(row=3, column=0, **pad)

        # VM Window
        ttk.Label(parent, text="VM Window:").grid(row=4, column=0, **pad)
        self.vm_var = tk.StringVar()
        self.vm_combo = ttk.Combobox(
            parent, textvariable=self.vm_var, state="readonly", width=28,
        )
        self.vm_combo.grid(row=5, column=0, **pad)
        ttk.Button(
            parent, text="Refresh Windows", command=self._refresh_vm_windows,
        ).grid(row=6, column=0, **pad)

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=7, column=0, pady=8, sticky="ew",
        )

        # Max actions
        ttk.Label(parent, text="Max actions:").grid(row=8, column=0, **pad)
        self.max_actions_var = tk.IntVar(value=self.config.max_actions)
        ttk.Spinbox(
            parent, from_=1, to=200, textvariable=self.max_actions_var, width=10,
        ).grid(row=9, column=0, **pad)

        # Action delay
        ttk.Label(parent, text="Action delay (sec):").grid(row=10, column=0, **pad)
        self.delay_var = tk.DoubleVar(value=self.config.action_delay)
        ttk.Spinbox(
            parent, from_=0.1, to=10.0, increment=0.1,
            textvariable=self.delay_var, width=10,
        ).grid(row=11, column=0, **pad)

        # Memory checkbox
        self.memory_var = tk.BooleanVar(value=self.config.memory_enabled)
        ttk.Checkbutton(
            parent, text="Enable AI Memory", variable=self.memory_var,
        ).grid(row=12, column=0, **pad)

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=13, column=0, pady=8, sticky="ew",
        )

        # Task input
        ttk.Label(parent, text="Task:").grid(row=14, column=0, **pad)
        self.task_text = tk.Text(parent, height=4, width=30, wrap=tk.WORD)
        self.task_text.grid(row=15, column=0, padx=10, pady=3, sticky="ew")

        # Bind Enter key to start (Shift+Enter for newline)
        self.task_text.bind("<Return>", self._on_task_enter)

        # Start / Stop buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=16, column=0, pady=10)
        self.start_btn = ttk.Button(
            btn_frame, text="Start", command=self._on_start,
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(
            btn_frame, text="Stop", command=self._on_stop, state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # Status
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(
            parent, textvariable=self.status_var, foreground="gray",
        ).grid(row=17, column=0, **pad)

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

        idx = self.vm_combo.current()
        if idx < 0 or idx >= len(self.vm_windows):
            messagebox.showwarning("No VM", "Please select a VM window.")
            return

        vm = self.vm_windows[idx]
        config = self._build_config_from_ui()

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
