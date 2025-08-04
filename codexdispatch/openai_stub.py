import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

_client = None


def openai_configure_api(api_key: Optional[str] = None):
    """Retrieve key, build global client, log success.

    Returns the configured OpenAI client or ``None`` if configuration fails.
    """
    global _client
    if _client is not None:
        return _client
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - import failure path
        logging.warning("openai package not available: %s", exc)
        return None
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        logging.warning("OPENAI_API_KEY is not set")
        return None
    _client = OpenAI(api_key=key)
    logging.info("OpenAI client configured")
    return _client


def openai_generate_response(
    *,
    messages: List[Dict[str, str]],
    functions: Optional[List[Dict[str, Any]]] = None,
    function_call: Optional[str | Dict[str, str]] = "auto",
    model: str = "o3-mini",
    reasoning_effort: str = "high",
    service_tier: str = "flex",
    **extra: Any,
):
    """Wrapper around ``client.chat.completions.create`` with defaults."""
    client = openai_configure_api()
    if client is None:
        raise RuntimeError("OpenAI client is not configured")
    params: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "reasoning_effort": reasoning_effort,
        "service_tier": service_tier,
        **extra,
    }
    if functions:
        params["functions"] = functions
        if function_call is not None:
            params["function_call"] = function_call
    logging.info("Sending:\n%s", messages)
    response = client.chat.completions.create(**params)
    logging.info("Received:\n%s", response)
    return response


def openai_parse_function_call(response: Any) -> Tuple[Optional[str], Any]:
    """Extract function call data from a chat completion response."""
    choice = response.choices[0] if response.choices else None
    msg = getattr(choice, "message", None) if choice else None
    fc = getattr(msg, "function_call", None) if msg else None
    if not fc:
        return None, None
    name = getattr(fc, "name", None)
    args_str = getattr(fc, "arguments", "") or "{}"
    try:
        data = json.loads(args_str)
    except json.JSONDecodeError:
        data = {}
    logging.info("Function call %s with %s", name, data)
    return name, data
