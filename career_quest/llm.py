"""Bounded Responses API transport with safe, actionable failure categories."""

import json
import os
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import Any

from career_quest.explain import MAX_RESPONSE_BYTES, TIMEOUT_SECONDS, _NoRedirect


class AIUnavailableError(ValueError):
    """Provider failure whose code can safely be shown without exposing credentials."""

    def __init__(self, code: str) -> None:
        """Store a sanitized error category."""
        self.code = code
        super().__init__(code)


def post(payload: dict[str, Any]) -> dict[str, Any]:
    """Send one bounded request without retries, redirects or raw error logging."""
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = {401: "credentials", 403: "access", 429: "quota", 400: "request", 404: "model"}.get(exc.code, "provider")
        raise AIUnavailableError(code) from None
    except (OSError, HTTPException) as exc:
        raise AIUnavailableError("connection") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AIUnavailableError("invalid_response")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise AIUnavailableError("invalid_response")
    return result


def output_text(body: dict[str, Any]) -> str:
    """Extract completed text and reject refusals, empty and malformed envelopes."""
    if body.get("status") != "completed":
        raise AIUnavailableError("incomplete")
    output = body.get("output")
    if not isinstance(output, list):
        raise AIUnavailableError("invalid_response")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if not isinstance(part, dict) or part.get("type") == "refusal":
                raise AIUnavailableError("refusal")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if not texts or not "".join(texts).strip():
        raise AIUnavailableError("empty")
    return "".join(texts)
