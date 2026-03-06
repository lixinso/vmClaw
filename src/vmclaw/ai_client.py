"""AI client - send screenshots to an AI vision model and get actions back."""

from __future__ import annotations

import base64
import json
from io import BytesIO

from openai import OpenAI
from PIL import Image

from .models import Action, Config

SYSTEM_PROMPT = """\
You are an AI agent controlling a virtual machine screen. You receive a screenshot \
of the VM and a task from the user. Your job is to determine the SINGLE next action \
to take toward completing the task.

Respond with a JSON object containing exactly one action. Available actions:

- {"action": "click", "x": <int>, "y": <int>, "reason": "<why>"}
  Left-click at the given coordinates (relative to the screenshot image).

- {"action": "type", "text": "<string>", "reason": "<why>"}
  Type the given text string.

- {"action": "key", "key": "<key_name>", "reason": "<why>"}
  Press a single key. Use names like: Return, Tab, Escape, Backspace, Delete, \
Up, Down, Left, Right, F1-F12, ctrl+a, ctrl+c, ctrl+v, alt+F4, etc.

- {"action": "scroll", "direction": "up" or "down", "reason": "<why>"}
  Scroll the mouse wheel up or down.

- {"action": "wait", "reason": "<why>"}
  Wait for the screen to update (e.g., a page is loading).

- {"action": "done", "reason": "<why>"}
  The task is complete.

Rules:
- Return ONLY valid JSON. No markdown, no explanation outside the JSON.
- The x,y coordinates are pixel positions on the screenshot image.
- Be precise with click coordinates - aim for the center of buttons/links/fields.
- Only return ONE action per response.
- If you cannot determine what to do, use "wait" to re-examine after a pause.
- Use "done" only when the task is clearly finished.
"""


GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"


def _create_client(config: Config) -> OpenAI:
    """Create an OpenAI-compatible client based on the configured provider.

    Supports:
        - "openai": Direct OpenAI API (default)
        - "github": GitHub Models API (uses GitHub PAT token)

    If api_base_url is set in config, it overrides the provider's default URL.
    """
    if config.provider == "github":
        api_key = config.github_token
        base_url = config.api_base_url or GITHUB_MODELS_BASE_URL
        if not api_key:
            raise ValueError(
                "GitHub provider selected but no token configured. "
                "Set GITHUB_TOKEN environment variable or github_token in config.toml"
            )
    else:
        api_key = config.openai_api_key
        base_url = config.api_base_url or None
        if not api_key:
            raise ValueError(
                "OpenAI provider selected but no API key configured. "
                "Set OPENAI_API_KEY environment variable or openai_api_key in config.toml"
            )

    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    return OpenAI(**kwargs)


def _image_to_base64(img: Image.Image, fmt: str = "PNG") -> str:
    """Convert a PIL Image to a base64-encoded data URL."""
    buf = BytesIO()
    img.save(buf, format=fmt)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    mime = "image/png" if fmt.upper() == "PNG" else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def ask_ai(
    screenshot: Image.Image,
    task: str,
    history: list[Action],
    config: Config,
) -> Action:
    """Send a screenshot and task to the AI model and get the next action.

    Args:
        screenshot: Current VM screenshot.
        task: User's task description.
        history: List of previous actions taken.
        config: Configuration with API key and model.

    Returns:
        The next Action to execute.

    Raises:
        ValueError: If the AI response cannot be parsed.
        openai.OpenAIError: On API errors.
    """
    client = _create_client(config)

    # Build history context
    history_text = ""
    if history:
        history_lines = []
        for i, a in enumerate(history, 1):
            history_lines.append(f"  Step {i}: {a.action.value} - {a.reason}")
        history_text = "\nActions taken so far:\n" + "\n".join(history_lines) + "\n"

    user_text = f"Task: {task}\n{history_text}\nWhat is the next action?"

    image_url = _image_to_base64(screenshot)

    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "high"},
                    },
                ],
            },
        ],
        max_completion_tokens=256,
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()

    # Try to extract JSON from the response (handle markdown code blocks)
    if raw.startswith("```"):
        # Strip ```json ... ``` wrapper
        lines = raw.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            elif line.startswith("```") and in_block:
                break
            elif in_block:
                json_lines.append(line)
        raw = "\n".join(json_lines)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse AI response as JSON: {raw!r}") from e

    return Action.from_dict(data)
