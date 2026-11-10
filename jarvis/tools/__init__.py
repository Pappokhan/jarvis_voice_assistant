"""Tool registry: every capability Jarvis has on this PC is a function registered with @tool.

Adding a capability = writing one decorated function in a module under jarvis/tools/ and
listing that module in MODULES below. The model sees the name, description and schema.
"""
from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

import jsonschema

log = logging.getLogger("jarvis.tools")

MODULES = ["apps", "files", "system", "desktop", "web", "shell", "memory_tools", "timers", "calc", "whatsapp"]


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    func: Callable[..., Any]
    danger: Callable[[dict], str | None] | None  # returns a spoken description if confirmation is needed


class Registry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}
        self.confirm_dangerous = True
        self.confirm_handler: Callable[[str], bool] | None = None
        # Shared context for tools: memory store, announce(text) for timers, config.
        self.ctx = SimpleNamespace(memory=None, announce=None, config=None)

    def api_tools(self) -> list[dict]:
        """Tool definitions for the Claude API (stable order keeps the prompt cache warm)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.schema,
                "eager_input_streaming": True,
            }
            for t in self.tools.values()
        ]

    def run(self, name: str, args: Any) -> tuple[Any, bool]:
        """Execute a tool. Returns (content, is_error); content is a string or content-block list."""
        t = self.tools.get(name)
        if t is None:
            return f"Unknown tool: {name}", True
        # Inputs are streamed without server-side validation, so validate here before acting.
        try:
            jsonschema.validate(args, t.schema)
        except jsonschema.ValidationError as e:
            return json.dumps({"INVALID_JSON": json.dumps(args), "problem": e.message}), True
        try:
            if t.danger and self.confirm_dangerous:
                what = t.danger(args)
                if what:
                    if self.confirm_handler is None or not self.confirm_handler(what):
                        return "The user did not confirm this action, so it was NOT performed.", True
            result = t.func(**args)
            if result is None:
                result = "Done."
            is_blocks = isinstance(result, list) and result and all(isinstance(b, dict) and "type" in b for b in result)
            if not isinstance(result, str) and not is_blocks:
                result = json.dumps(result, ensure_ascii=False, default=str)
            if isinstance(result, str) and len(result) > 12000:
                result = result[:12000] + "\n...[truncated]"
            return result, False
        except Exception as e:  # tool failures go back to the model so it can adapt
            log.warning("tool %s failed: %s: %s", name, type(e).__name__, e)
            log.debug("traceback", exc_info=True)
            return f"{type(e).__name__}: {e}", True


registry = Registry()


def tool(
    description: str,
    params: dict[str, dict] | None = None,
    required: list[str] | None = None,
    danger: Callable[[dict], str | None] | None = None,
):
    """Register a function as a tool. `params` maps argument name -> JSON schema."""

    def deco(func):
        params_ = params or {}
        registry.tools[func.__name__] = Tool(
            name=func.__name__,
            description=description,
            schema={
                "type": "object",
                "properties": params_,
                "required": list(params_) if required is None else required,
                "additionalProperties": False,
            },
            func=func,
            danger=danger,
        )
        return func

    return deco


def S(desc: str, **extra) -> dict:
    return {"type": "string", "description": desc, **extra}


def N(desc: str, **extra) -> dict:
    return {"type": "number", "description": desc, **extra}


def B(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


NEEDS_VISION = ("look_at_screen", "click", "scroll")  # useless to a model that cannot see images


def load_all(local: bool = False) -> Registry:
    """local=True prepares the tool set for a small local model: adds free web search, drops vision tools."""
    for mod in MODULES + (["websearch"] if local else []):
        importlib.import_module(f"jarvis.tools.{mod}")
    if local:
        for name in NEEDS_VISION:
            registry.tools.pop(name, None)
    return registry
