"""Vision inference through an OpenAI-compatible chat-completions API."""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request

import scene_config


def ensure_configured() -> tuple[str, str, str]:
    base_url = scene_config.resolved("API_BASE_URL")
    model = scene_config.resolved("MODEL_NAME")
    if not base_url or not model:
        raise RuntimeError(
            "scene_config.API_BASE_URL and MODEL_NAME must be set "
            "(or SCENE_API_BASE_URL / SCENE_MODEL_NAME in the environment)"
        )
    return base_url.rstrip("/"), model, scene_config.resolved("API_KEY")


def image_url(image) -> str:
    copy = image.convert("RGB")
    copy.thumbnail((scene_config.IMAGE_MAX_EDGE, scene_config.IMAGE_MAX_EDGE))
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=scene_config.JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def complete(prompt: str, images: list[tuple[str, object]]) -> tuple[str, int | None]:
    """Send a prompt plus labelled images; return (reply text, completion tokens)."""
    base_url, model, key = ensure_configured()
    content = [{"type": "text", "text": prompt}]
    for label, image in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {"url": image_url(image)}})
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": scene_config.TEMPERATURE,
        "max_tokens": scene_config.MAX_OUTPUT_TOKENS,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", **scene_config.EXTRA_HEADERS}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    error = "no attempt"
    for attempt in range(scene_config.MAX_RETRIES + 1):
        if attempt:
            time.sleep(min(2 ** attempt, 10))
        request = urllib.request.Request(
            f"{base_url}/chat/completions", data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=scene_config.REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            error = f"HTTP {exc.code}: {detail}"
            if exc.code == 429 or exc.code >= 500:
                continue
            raise RuntimeError(f"Vision API request failed: {error}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            continue
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Vision API returned invalid JSON: {exc}") from exc
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Vision API response has no choices[0].message.content") from exc
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        usage = payload.get("usage") or {}
        return str(text or "").strip(), usage.get("completion_tokens")
    raise RuntimeError(f"Vision API request failed after retries: {error}")
