"""Orchestrator - the capture -> think -> act agent loop."""

from __future__ import annotations

import time

from .ai_client import ask_ai
from .capture import capture_and_resize
from .executor import execute_action
from .models import Action, ActionType, Config, VMWindow


def run_task(
    vm: VMWindow,
    task: str,
    config: Config,
) -> list[Action]:
    """Run the agent loop: capture screenshot, ask AI, execute action, repeat.

    Args:
        vm: The target VM window.
        task: User's task description.
        config: Configuration.

    Returns:
        List of actions that were executed.
    """
    history: list[Action] = []
    consecutive_waits = 0
    max_consecutive_waits = 5

    print(f"\nStarting task: {task}")
    print(f"Target: {vm.title}")
    print(f"Max actions: {config.max_actions}\n")

    for step in range(1, config.max_actions + 1):
        # 1. Capture screenshot
        print(f"[Step {step}] Capturing screen...")
        img = capture_and_resize(vm.hwnd, target_width=config.screenshot_width)
        if img is None:
            print(f"[Step {step}] Failed to capture screenshot. Retrying...")
            time.sleep(1.0)
            img = capture_and_resize(vm.hwnd, target_width=config.screenshot_width)
            if img is None:
                print(f"[Step {step}] Screenshot capture failed. Aborting.")
                break

        img_width, img_height = img.size

        # 2. Ask AI for next action
        print(f"[Step {step}] Analyzing screen...")
        try:
            action = ask_ai(img, task, history, config)
        except Exception as e:
            print(f"[Step {step}] AI error: {e}")
            print(f"[Step {step}] Retrying...")
            time.sleep(1.0)
            try:
                action = ask_ai(img, task, history, config)
            except Exception as e2:
                print(f"[Step {step}] AI error on retry: {e2}. Aborting.")
                break

        # 3. Display the action
        action_desc = _format_action(action)
        print(f"[Step {step}] -> {action_desc}")

        # 4. Check for done
        if action.action == ActionType.DONE:
            print(f"\nTask complete. {len(history)} actions taken.")
            history.append(action)
            return history

        # 5. Track consecutive waits (stuck detection)
        if action.action == ActionType.WAIT:
            consecutive_waits += 1
            if consecutive_waits >= max_consecutive_waits:
                print(
                    f"\n[Step {step}] Stuck: {max_consecutive_waits} consecutive "
                    f"waits. Aborting."
                )
                break
        else:
            consecutive_waits = 0

        # 6. Execute the action
        try:
            execute_action(vm.hwnd, action, img_width, img_height)
            print(f"[Step {step}] Executed")
        except Exception as e:
            print(f"[Step {step}] Execution error: {e}")

        history.append(action)

        # 7. Wait between actions
        time.sleep(config.action_delay)

    print(f"\nTask stopped after {len(history)} actions (limit: {config.max_actions}).")
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
