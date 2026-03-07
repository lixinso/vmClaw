# vmClaw - Product Requirements Document

## 1. Overview

**Product Name:** vmClaw
**Version:** 0.1 (MVP)
**Date:** 2026-03-05

vmClaw is a Windows host-side tool that uses AI vision models to autonomously operate virtual machine screens. It captures the VM window, sends screenshots to an AI model, and executes the returned actions (click, type, keypress, scroll) on the VM — enabling natural-language control of any VM without guest agents.

## 2. Problem Statement

Controlling virtual machines is manual and tedious. Users must visually interpret the VM screen, move their mouse into the VM window, and perform repetitive tasks (installing software, configuring systems, running test sequences) by hand.

Existing automation tools (Ansible, Puppet, SSH scripts) require network access and a running OS with agents. They don't work for:

- OS installation / setup wizards
- GUI-only applications
- Environments with no network or SSH access
- Pre-boot / BIOS / UEFI configuration
- Testing how a real user would interact with the system

## 3. Target Users

| Persona | Use Case |
|---|---|
| **DevOps / IT Admins** | Automate repetitive VM provisioning, OS installs, configuration |
| **QA Engineers** | GUI test automation inside VMs without guest agents |
| **Security Researchers** | Interact with sandboxed malware VMs without network exposure |
| **Developers** | Quick "set up this VM for me" without manual clicking |
| **Home Lab Enthusiasts** | Manage multiple VMs without clicking through each one |

## 4. Core Value Proposition

**Control any VM through its screen, using natural language, with zero guest installation.**

The tool treats VMs the same way a human operator would — by looking at the screen and using mouse/keyboard — but powered by AI vision.

## 5. Architecture

```
+--------------------------------------------------+
|                 vmClaw CLI / UI                   |
|          (User chat, task input, preview)         |
+-------------------------+------------------------+
                          |
             +------------v-----------+
             |      Orchestrator      |
             |   (Agent loop + state) |
             +---+--------+------+---+
                 |        |      |
       +---------v--+ +---v----+ +v--------------+
       |   Screen   | |   AI   | |    Action     |
       |   Capture  | | Client | |    Executor   |
       |   Module   | |(OpenAI)| | (mouse/kbd)   |
       +------------+ +--------+ +---------------+
```

**Agent Loop:**

```
while task not done:
    screenshot = capture_vm_window(hwnd)
    action = ask_ai(screenshot, task, history)
    if action.type == "done":
        break
    execute_action(hwnd, action)
    history.append(action)
```

## 6. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| F1 | Discover and list VM windows running on the host | Must |
| F2 | Capture screenshot of a selected VM window | Must |
| F3 | Send screenshot + task to an AI vision model | Must |
| F4 | Parse AI response into executable actions (click, type, keypress, scroll) | Must |
| F5 | Execute actions on the VM window (coordinate-mapped input) | Must |
| F6 | Run an autonomous agent loop (capture -> think -> act -> repeat) | Must |
| F7 | Accept user task/goal via CLI chat | Must |
| F8 | Stop/pause execution on user command or hotkey | Must |
| F9 | Show live screenshot preview to user during execution | Should |
| F10 | Log all actions with screenshots for audit/replay | Should |
| F11 | Confirm destructive actions before executing | Should |
| F12 | Support multiple AI backends (OpenAI, Anthropic, etc.) | Should |
| F13 | Resume a partially completed task | Could |
| F14 | Parallel control of multiple VMs | Could |
| F15 | Web UI for remote monitoring/control | Won't (v1) |

## 7. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NF1 | Action latency < 3s per step (capture + AI call + execution) |
| NF2 | Works with Hyper-V, VMware Workstation, VirtualBox windows |
| NF3 | No installation required inside the guest VM |
| NF4 | Runs on Windows 10/11 |
| NF5 | API keys stored securely (not plaintext in code) |
| NF6 | Graceful failure — a bad AI response must not crash the tool |

## 8. Action Schema

The AI model returns one action per step as structured JSON:

| Action | Parameters | Description |
|---|---|---|
| `click` | `x`, `y` | Left-click at coordinates |
| `type` | `text` | Type a string of text |
| `key` | `name` | Press a key (e.g., Return, Tab, Escape) |
| `scroll` | `direction` (`up`/`down`) | Scroll the mouse wheel |
| `wait` | — | Wait and re-capture (screen is loading) |
| `done` | — | Task is complete |

Each action includes a `reason` field for logging/explainability.

Example AI response:
```json
{"action": "click", "x": 512, "y": 384, "reason": "Clicking the Start menu"}
```

## 9. Coordinate Mapping

The AI sees image coordinates (e.g., 1024x768 screenshot). These must be mapped to actual screen coordinates:

```
actual_x = window_left + (ai_x / screenshot_width) * window_width
actual_y = window_top  + (ai_y / screenshot_height) * window_height
```

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| AI hallucinates wrong coordinates | Clicks wrong element, corrupts VM state | Max retries, screenshot diff validation |
| AI gets stuck in a loop | Wastes API tokens, no progress | Loop detection (same screenshot N times -> stop), max action limit |
| VM window is resized/moved mid-task | Coordinates become wrong | Re-acquire window rect before each action |
| API cost | Heavy screenshot traffic to model | Compress images, reduce resolution, cache unchanged screens |
| Input injection doesn't reach VM | Actions fail silently | Verify screen changed after action, retry logic |

## 11. Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| Language | Python 3.11+ | Best ecosystem for AI APIs, screenshot libs, Windows automation |
| Screen Capture | `mss` / Win32 API (`win32gui` + `win32ui`) | Capture specific windows by HWND, fast |
| VM Window Discovery | `pywin32` (`win32gui.EnumWindows`) | Enumerate windows, filter by VM process names |
| Input Injection | `pyautogui` / `win32api.SendInput` | Coordinates relative to VM window |
| AI Model Client | `openai` Python SDK | Supports vision (image input), structured output |
| CLI Interface | `rich` | Good terminal UX with minimal effort |
| Config | TOML | Store API keys, VM preferences, safety settings |

## 12. Success Metrics (MVP)

- Can complete a 10-step manual task (e.g., open Notepad, type text, save file) autonomously in a VM
- Works across at least 2 hypervisor types (e.g., Hyper-V and VirtualBox)
- < 5% action failure rate on clear, well-lit VM screens

---

# MVP Specification

## Scope

The MVP answers one question: **Can AI look at a VM screen and successfully operate it?**

## What's IN the MVP

| Component | MVP Scope | Excluded |
|---|---|---|
| **VM Discovery** | Enumerate windows by title keyword matching | No hypervisor API integration |
| **Screen Capture** | Region capture using window coordinates | No occluded window capture |
| **AI Client** | OpenAI API only, single system prompt | No model switching, no prompt tuning UI |
| **Action Schema** | `click`, `type`, `key`, `scroll`, `wait`, `done` | No drag, no multi-touch, no file transfer |
| **Executor** | `pyautogui` mouse/keyboard relative to window | No `SendInput` low-level, no background input |
| **Agent Loop** | Synchronous: capture -> AI -> act -> repeat | No parallelism, no branching |
| **CLI** | Simple input loop: user types task, sees status | No TUI, no web UI, no live preview |
| **Safety** | Max 50 actions per task, Ctrl+C to abort | No destructive action detection |
| **Logging** | Print actions to console | No screenshot history, no replay |

## MVP User Flow

```
$ python -m vmclaw

vmClaw - VM Computer Use Agent
Discovering VM windows...

  [1] VirtualBox - Ubuntu 22.04
  [2] vmconnect - Windows Server 2022

Select VM [1-2]: 1

Captured: 1024x768 screenshot of "VirtualBox - Ubuntu 22.04"

Enter task: Open the terminal and run "uname -a"

[Step 1] Analyzing screen...
[Step 1] -> click(512, 740) - "Clicking Activities in top-left corner"
[Step 1] Executed

[Step 2] Analyzing screen...
[Step 2] -> type("terminal") - "Typing 'terminal' in search"
[Step 2] Executed

[Step 3] Analyzing screen...
[Step 3] -> click(510, 300) - "Clicking Terminal app icon"
[Step 3] Executed

[Step 4] Analyzing screen...
[Step 4] -> type("uname -a") - "Typing the command"
[Step 4] Executed

[Step 5] Analyzing screen...
[Step 5] -> key("Return") - "Pressing Enter to execute"
[Step 5] Executed

[Step 6] Analyzing screen...
[Step 6] -> done - "Command output is visible on screen"

Task complete. 6 actions taken.
Enter task (or 'quit'):
```

## MVP Project Structure

```
vmClaw/
├── pyproject.toml
├── config.example.toml
├── README.md
├── PRD/
│   └── PRD.md
└── src/
    └── vmclaw/
        ├── __init__.py
        ├── main.py              # CLI entry point + main loop
        ├── discovery.py         # VM window enumeration
        ├── capture.py           # Screenshot capture
        ├── ai_client.py         # OpenAI API wrapper
        ├── executor.py          # Mouse/keyboard action execution
        ├── models.py            # Action dataclass, config model
        └── orchestrator.py      # Agent loop (capture -> think -> act)
```

## MVP Dependencies

```
openai >= 1.0
pyautogui >= 0.9
mss >= 9.0
pywin32 >= 306
Pillow >= 10.0
tomli >= 2.0  (for config parsing on Python < 3.11)
```

---

# Post-MVP Roadmap

| Priority | Feature | Description |
|---|---|---|
| 1 | Screenshot diff detection | Skip AI call if screen hasn't changed (saves cost + latency) |
| 2 | Occluded window capture | Use `PrintWindow` so VM doesn't need to be in foreground |
| 3 | Action history + replay | Save screenshots + actions for debugging and audit |
| 4 | Multi-model support | Adapter for Anthropic Claude, local models (e.g., LLaVA) |
| 5 | Live TUI preview | Show the VM screenshot in terminal during execution |
| 6 | Destructive action confirmation | Pattern matching on "delete", "format", "shutdown" |
| 7 | Hypervisor API integration | Send Ctrl+Alt+Del, snapshot/restore via Hyper-V/VMware APIs |
| 8 | Web UI | Remote monitoring and control via browser |
| 9 | Multi-VM orchestration | Run tasks across multiple VMs in parallel |
