"""Orchestrator - the capture -> think -> act agent loop."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from .ai_client import ask_ai
from .capture import capture_and_resize
from .executor import execute_action
from .models import Action, ActionType, Config, TokenUsage, VMWindow

if TYPE_CHECKING:
    from .memory import MemoryStore


def _emit(
    on_event: Callable[[str, Any], None] | None,
    event_type: str,
    data: Any = None,
) -> None:
    """Emit a structured event to the callback, or print if no callback."""
    if on_event is not None:
        on_event(event_type, data)
    elif event_type == "log":
        print(data)


def _check_repeated_actions(history: list[Action], threshold: int = 5) -> str:
    """Detect if the last N actions are repetitive and return a hint if so.

    Checks if the last `threshold` actions are the same type (e.g., all clicks
    on similar coordinates). Returns a warning string to inject into the prompt,
    or empty string if no repetition detected.
    """
    if len(history) < threshold:
        return ""

    recent = history[-threshold:]

    # Check if all recent actions have the same type
    action_types = {a.action for a in recent}
    if len(action_types) != 1:
        return ""

    action_type = recent[0].action

    if action_type == ActionType.CLICK:
        return (
            "\nWARNING: You have clicked the same area multiple times in a row "
            "without progress. The click is working but you need to take a "
            "DIFFERENT action now. If you clicked a text field or address bar, "
            "use key(\"ctrl+a\") to select all text, then type(\"...\") to enter "
            "new text, then key(\"Return\") to confirm. Do NOT click again.\n"
        )
    elif action_type == ActionType.KEY:
        return (
            "\nWARNING: You have pressed the same key multiple times without "
            "progress. Try a different approach to complete the task.\n"
        )
    elif action_type == ActionType.TYPE:
        return (
            "\nWARNING: You have typed text multiple times without progress. "
            "Check if the text field is focused and try clicking it first.\n"
        )

    return ""


def run_task(
    vm: VMWindow,
    task: str,
    config: Config,
    memory: MemoryStore | None = None,
    on_event: Callable[[str, Any], None] | None = None,
    stop_event: threading.Event | None = None,
) -> list[Action]:
    """Run the agent loop: capture screenshot, ask AI, execute action, repeat.

    Args:
        vm: The target VM window.
        task: User's task description.
        config: Configuration.
        memory: Optional memory store for recalling similar past tasks.
        on_event: Optional callback for structured events. When None, uses print().
        stop_event: Optional threading.Event to signal early stop from another thread.

    Returns:
        List of actions that were executed.
    """
    history: list[Action] = []
    consecutive_waits = 0
    max_consecutive_waits = 15
    outcome = "max_actions"
    total_usage = TokenUsage()

    # Search memory for similar past tasks
    memory_context = ""
    if memory is not None:
        try:
            similar = memory.search_similar(task, config)
            if similar:
                memory_context = memory.format_memory_context(similar)
                _emit(on_event, "log", f"[Memory] Found {len(similar)} similar past task(s).")
        except Exception as e:
            _emit(on_event, "log", f"[Memory] Search failed (non-fatal): {e}")

    _emit(on_event, "log", f"\nStarting task: {task}")
    _emit(on_event, "log", f"Target: {vm.title}")
    _emit(on_event, "log", f"Max actions: {config.max_actions}\n")

    for step in range(1, config.max_actions + 1):
        # Check for stop signal
        if stop_event is not None and stop_event.is_set():
            _emit(on_event, "log", "\nTask stopped by user.")
            outcome = "stopped"
            break

        _emit(on_event, "step", step)

        # 1. Capture screenshot
        _emit(on_event, "log", f"[Step {step}] Capturing screen...")
        img = capture_and_resize(vm.hwnd, target_width=config.screenshot_width)
        if img is None:
            _emit(on_event, "log", f"[Step {step}] Failed to capture screenshot. Retrying...")
            time.sleep(1.0)
            img = capture_and_resize(vm.hwnd, target_width=config.screenshot_width)
            if img is None:
                _emit(on_event, "log", f"[Step {step}] Screenshot capture failed. Aborting.")
                outcome = "error"
                break

        _emit(on_event, "screenshot", img)
        img_width, img_height = img.size

        # 2. Ask AI for next action
        _emit(on_event, "log", f"[Step {step}] Analyzing screen...")
        stuck_hint = _check_repeated_actions(history)
        if stuck_hint:
            _emit(on_event, "log", f"[Step {step}] Detected repeated actions, adding hint.")
        effective_context = memory_context + stuck_hint
        try:
            action, usage = ask_ai(
                img, task, history, config, memory_context=effective_context,
            )
            total_usage.prompt_tokens += usage.prompt_tokens
            total_usage.completion_tokens += usage.completion_tokens
            total_usage.total_tokens += usage.total_tokens
            _emit(on_event, "tokens", total_usage)
        except Exception as e:
            _emit(on_event, "log", f"[Step {step}] AI error: {e}")
            _emit(on_event, "log", f"[Step {step}] Retrying...")
            time.sleep(1.0)
            try:
                action, usage = ask_ai(
                    img, task, history, config, memory_context=effective_context,
                )
                total_usage.prompt_tokens += usage.prompt_tokens
                total_usage.completion_tokens += usage.completion_tokens
                total_usage.total_tokens += usage.total_tokens
                _emit(on_event, "tokens", total_usage)
            except Exception as e2:
                _emit(on_event, "log", f"[Step {step}] AI error on retry: {e2}. Aborting.")
                outcome = "error"
                break

        # 3. Display the action
        action_desc = _format_action(action)
        _emit(on_event, "log", f"[Step {step}] -> {action_desc}")
        _emit(on_event, "action", action)

        # 4. Check for done
        if action.action == ActionType.DONE:
            _emit(on_event, "log", f"\nTask complete. {len(history)} actions taken.")
            history.append(action)
            outcome = "done"
            break

        # 5. Track consecutive waits (stuck detection)
        if action.action == ActionType.WAIT:
            consecutive_waits += 1
            if consecutive_waits >= max_consecutive_waits:
                _emit(
                    on_event, "log",
                    f"\n[Step {step}] Stuck: {max_consecutive_waits} consecutive "
                    f"waits. Aborting.",
                )
                outcome = "interrupted"
                break
            # Progressive delay: wait longer as consecutive waits increase
            # 1st-3rd wait: normal delay, 4th-6th: 2x, 7th+: 3x
            if consecutive_waits >= 7:
                extra = config.action_delay * 2
            elif consecutive_waits >= 4:
                extra = config.action_delay
            else:
                extra = 0
            if extra > 0:
                _emit(on_event, "log", f"[Step {step}] Waiting longer ({config.action_delay + extra:.1f}s)...")
                time.sleep(extra)
        else:
            consecutive_waits = 0

        # 6. Execute the action
        try:
            execute_action(vm.hwnd, action, img_width, img_height)
            _emit(on_event, "log", f"[Step {step}] Executed")
        except Exception as e:
            _emit(on_event, "log", f"[Step {step}] Execution error: {e}")

        history.append(action)

        # 7. Wait between actions
        time.sleep(config.action_delay)
    else:
        _emit(on_event, "log", f"\nTask stopped after {len(history)} actions (limit: {config.max_actions}).")

    # Save to memory
    if memory is not None:
        try:
            task_id = memory.save_task(task, vm.title, outcome, history, config)
            if task_id:
                _emit(on_event, "log", f"[Memory] Saved task run #{task_id} (outcome: {outcome})")
        except Exception as e:
            _emit(on_event, "log", f"[Memory] Save failed (non-fatal): {e}")

    _emit(on_event, "done", outcome)
    return history


def _format_action(action: Action) -> str:
    """Format an action for display."""
    a = action.action.value
    reason = f' - "{action.reason}"' if action.reason else ""

    if action.action == ActionType.CLICK:
        return f'click({action.x}, {action.y}){reason}'
    elif action.action == ActionType.TYPE:
        text = action.text or ""
        if len(text) > 40:
            text = text[:40] + "..."
        return f'type("{text}"){reason}'
    elif action.action == ActionType.KEY:
        return f'key("{action.key}"){reason}'
    elif action.action == ActionType.SCROLL:
        return f'scroll({action.direction}){reason}'
    elif action.action == ActionType.WAIT:
        return f'wait{reason}'
    elif action.action == ActionType.DONE:
        return f'done{reason}'
    else:
        return f'{a}{reason}'
