"""Configuration: defaults deep-merged with config.yaml, plus .env loading."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
LOG_DIR = ROOT / "logs"

DEFAULTS: dict = {
    "assistant": {
        "name": "Jarvis",
        "honorific": "sir",
        "follow_up": "questions",
        "follow_up_seconds": 5,
        "hotkey": "ctrl+alt+j",
    },
    "brain": {
        "provider": "ollama",
        "local": {"model": "qwen2.5:3b", "url": "http://127.0.0.1:11434", "num_ctx": 8192, "think": False},
        "model": "claude-opus-5-5",
        "effort": "low",
        "web_search": True,
        "refusal_fallback": True,
        "session_idle_minutes": 15,
        "max_steps": 30,
    },
    "wake_word": {"model": "hey_jarvis", "threshold": 0.5},
    "stt": {
        "model": "small.en",
        "cpu_model": "small.en",
        "device": "auto",
        "language": "en",
        "hints": "Glossary: Jarvis, Chrome, Notepad, Calculator, YouTube, Wi-Fi, Bluetooth, dictation.",
    },
    "tts": {"voice": "en-GB-RyanNeural", "rate": "+8%", "pitch": "-4Hz"},
    "audio": {
        "input_device": None,
        "output_device": None,
        "energy_threshold": "auto",
        "silence_seconds": 0.75,
        "max_command_seconds": 20,
    },
    "safety": {"confirm_dangerous": True},
    "ui": {"enabled": True, "port": 8765},
}


def _merge(base: dict, over: dict) -> dict:
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: Path | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    for d in (DATA_DIR, MODELS_DIR, LOG_DIR):
        d.mkdir(exist_ok=True)
    cfg = copy.deepcopy(DEFAULTS)
    path = path or ROOT / "config.yaml"
    if path.exists():
        _merge(cfg, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return cfg
