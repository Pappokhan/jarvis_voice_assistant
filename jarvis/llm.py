"""Chooses the brain: a free local model (Ollama) or Claude (cloud, needs an API key)."""
from __future__ import annotations


def is_local(cfg: dict) -> bool:
    return cfg["brain"]["provider"] != "claude"


def create_brain(cfg: dict, memory, registry):
    if is_local(cfg):
        from .brain_local import LocalBrain

        return LocalBrain(cfg, memory, registry)
    from .brain import Brain

    return Brain(cfg, memory, registry)
