"""A completely free, offline brain: a small local model served by Ollama on this PC.

Same interface as brain.Brain, so the rest of the assistant does not care which one is in use.
No API key, no cost. With the desktop's own VRAM use, a 4 GB GPU fully fits a 3-billion-parameter
model, which handles everyday commands well but is less reliable than a large cloud model on long
multi-step tasks. Models that always "think" (the current qwen3:4b) take a minute per command here.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

import requests

from .brain import SYSTEM_PROMPT
from .memory import Memory
from .tools import Registry

log = logging.getLogger("jarvis.brain")

LOCAL_RULES = """

Rules that matter most:
- To do anything on the PC you must call a tool. Never say you did something unless a tool call did it.
- Call one tool at a time and wait for its result. Use the exact argument names from the tool definition.
- If a tool returns an error, read it, fix the arguments and try once more, or tell the user what went wrong.
- For facts you are not sure of, or anything recent, call web_search instead of guessing.
- Questions about the PC's current state (volume, brightness, battery, open windows, files) also need a tool:
  call set_volume or set_brightness with no arguments to read the current level, and set_radio with only
  "radio" to check Wi-Fi or Bluetooth. Pass "on", "level" or "mute" only when asked to change something.
- Keep the spoken reply to one or two short sentences, and answer only what was asked: from a long tool
  result, mention just the part the user wanted.
- type_text is only for words the user asked you to type or write. Never use it to answer or talk to the user.
- To message someone, call send_whatsapp. If you do not know who or what to send, ask; do not guess.
- What you receive is speech recognition and can be garbled. If it makes no sense or is not a request to you,
  call no tool and reply only: "Sorry, I did not catch that."
- Talk to the user directly as "you". Never say "the user".
- Never end with an offer of more help such as "Is there anything else?"."""


# Repeated with every request: a small model follows the most recent instruction far better than the system prompt.
REMINDER = (
    "(Before you answer: if this asks you to do something, or asks about the PC, files, reminders, weather or any "
    "fact, call the tool now, even if you did the same thing earlier. Never answer those from memory: your knowledge is years old, so anything about the latest, last, current or newest "
    "of something, sport, news or prices needs web_search. Do only what this message asks. Greetings and small talk need no tool.)"
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_OFFER = re.compile(
    r"(anything else|anything specific|assist you|help you with|would you like|what would you like|"
    r"how (else )?(can|may) i|let me know if)[^.!?]*[?.!]\s*$", re.I)


_CALL_TAG = "<tool_call>"
_LIVE = re.compile(
    r"\b(volume|loud|mute|bright|battery|charg|ram|memory usage|cpu|disk|storage|wi-?fi|bluetooth|weather|rain|temperature|"
    r"forecast|remind|window|clipboard|folder|file|download|open|close|launch|start|play|pause|delete|create|search|"
    r"latest|current|news|price|shut ?down|restart|lock|sleep|screenshot|type|press)", re.I)


def _parse_calls(raw: str) -> list[dict]:
    """Every {"name": ..., "arguments": {...}} object the model wrote after a <tool_call> tag."""
    calls = []
    for piece in raw.split(_CALL_TAG)[1:]:
        start = piece.find("{")
        if start < 0:
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(piece[start:])
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("name"), str):
            args = obj.get("arguments", obj.get("parameters"))
            calls.append({"name": obj["name"], "arguments": args if isinstance(args, dict) else {}})
    return calls


def _coerce(args: dict, schema: dict) -> dict:
    """Small models often send "40" for a number or "true" for a boolean; repair that before validation."""
    props = schema.get("properties", {})
    out = {}
    for key, value in (args or {}).items():
        if value is None or key not in props:
            continue
        kind = props[key].get("type")
        if isinstance(value, str):
            v = value.strip()
            try:
                if kind == "number":
                    value = float(v.rstrip("%"))
                elif kind == "boolean":
                    value = v.lower() in ("true", "yes", "1", "on")
                elif kind == "array":
                    value = json.loads(v) if v.startswith("[") else [v]
            except ValueError:
                pass
        elif kind == "string" and not isinstance(value, str):
            value = str(value)
        out[key] = value
    return out


class LocalBrain:
    def __init__(self, cfg: dict, memory: Memory, registry: Registry):
        self.cfg = cfg
        self.memory = memory
        self.registry = registry
        c = cfg["brain"]["local"]
        self.model = c["model"]
        self.url = c["url"].rstrip("/")
        self.num_ctx = int(c["num_ctx"])
        self.think = bool(c["think"])
        self.idle_seconds = cfg["brain"]["session_idle_minutes"] * 60
        self.max_steps = min(cfg["brain"]["max_steps"], 8)
        self.lock = threading.Lock()
        self.messages: list[dict] = []
        self._tools: list[dict] = []
        self._last_used = 0.0
        self._send_think = True

    # ---------- server ----------
    def server_up(self) -> bool:
        try:
            return requests.get(self.url + "/api/version", timeout=2).ok
        except requests.RequestException:
            return False

    def ensure_server(self) -> bool:
        """Ollama normally runs in the tray; if it isn't up, start it."""
        if self.server_up():
            return True
        exe = shutil.which("ollama") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if not os.path.exists(exe):
            return False
        subprocess.Popen([exe, "serve"], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            time.sleep(0.5)
            if self.server_up():
                return True
        return False

    def warm_up(self) -> None:
        """Load the model into the GPU now so the first request isn't slow."""
        try:
            if self.ensure_server():
                # Also run the long system prompt through once: Ollama keeps it cached, so the first real
                # request does not spend five seconds reading the tool list.
                with self.lock:
                    if not self.messages:
                        self._new_session()
                        self._last_used = time.time()
                    primer = self.messages[:1] + [{"role": "user", "content": "hello"}]
                requests.post(self.url + "/api/chat", timeout=180, json={
                    "model": self.model, "messages": primer, "stream": False, "keep_alive": "30m",
                    "options": {"num_ctx": self.num_ctx, "temperature": 0.1, "num_predict": 1}})
        except requests.RequestException:
            pass

    # ---------- session ----------
    def _new_session(self) -> None:
        a = self.cfg["assistant"]
        hon = a.get("honorific") or ""
        base = SYSTEM_PROMPT.format(
            name=a["name"], user=getpass.getuser(),
            address=f'Address them as "{hon}", sparingly.' if hon else "",
        )
        facts = "\n".join(f"[{i}] {t}" for i, t in self.memory.facts(40)) or "(nothing saved yet)"
        recent = "\n".join(
            f"{'User' if role == 'user' else 'You'}: {text[:200]}" for _, role, text in self.memory.recent_log(6)
        ) or "(this is your first conversation)"
        # The tool list goes into the prompt in the model's own format and the calls are parsed here, because
        # Ollama's parser silently drops some valid calls (for example one with empty arguments).
        signatures = "\n".join(
            json.dumps({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}})
            for t in self.registry.tools.values()
        )
        system = (
            f"{base}{LOCAL_RULES}\n\nLong-term memory (id, fact):\n{facts}\n\nEnd of your previous conversation:\n{recent}"
            "\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
            f"You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n{signatures}\n</tools>\n\n"
            "For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n"
            '<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'
        )
        self.messages = [{"role": "system", "content": system}]
        log.info("new local session: %s, %d tools", self.model, len(self.registry.tools))

    def reset(self) -> None:
        self.messages = []

    def _trim(self) -> None:
        """Keep the context small: system prompt plus the most recent turns, cut at a user message."""
        # A long history makes a small model answer from memory instead of calling tools, so keep three turns.
        users = [i for i, m in enumerate(self.messages) if m["role"] == "user" and m["content"].startswith("[")]
        if len(users) > 3:
            self.messages = [self.messages[0]] + self.messages[users[-3]:]
        # Earlier turns answered without a tool (small talk) are dropped, except the latest: after a few of
        # them the model copies the pattern and starts inventing answers instead of calling tools.
        users = [i for i, m in enumerate(self.messages) if m["role"] == "user" and m["content"].startswith("[")]
        for start, end in reversed(list(zip(users[:-1], users[1:]))):
            if end - start == 2:
                del self.messages[start:end]
        for m in self.messages:  # the reminder belongs only on the newest request
            if m["role"] == "user" and m["content"].endswith(REMINDER):
                m["content"] = m["content"][: -len(REMINDER)].rstrip()

    # ---------- one model call ----------
    def _chat(self, emit: Callable[[str], None], cancel: threading.Event):
        body = {
            "model": self.model,
            "messages": self.messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {"num_ctx": self.num_ctx, "temperature": 0.1},
        }
        if self._send_think:
            body["think"] = self.think
        r = requests.post(self.url + "/api/chat", json=body, stream=True, timeout=(5, 300))
        if r.status_code == 400 and self._send_think and "think" in r.text.lower():
            self._send_think = False  # this model has no thinking switch
            return self._chat(emit, cancel)
        if r.status_code == 404:
            raise LookupError(r.text)
        r.raise_for_status()
        content, raw = "", ""

        def release(final: bool) -> None:
            """Speak finished sentences (never tool-call text), keeping the last one back so a closing
            "anything else?" can be dropped."""
            nonlocal content
            speech = raw.split(_CALL_TAG, 1)[0]
            if not final and "<" in speech:
                speech = speech[: speech.rfind("<")]  # may be the start of a tool-call tag
            parts = _SENTENCE_END.split(speech.strip())
            if final:
                while parts and (not parts[-1].strip() or (_OFFER.search(parts[-1]) and len(parts) > 1)):
                    parts.pop()
                target = " ".join(parts)
            elif len(parts) > 2:
                target = " ".join(parts[:-2]) + " "
            else:
                return
            if len(target) > len(content) and target.startswith(content):
                out, content = target[len(content):], target
                emit(out)

        with r:
            for line in r.iter_lines():
                if cancel.is_set():
                    return None
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                msg = chunk.get("message") or {}
                if msg.get("content"):
                    raw += msg["content"]
                    release(False)
                if chunk.get("done"):
                    log.debug("tokens in=%s out=%s", chunk.get("prompt_eval_count"), chunk.get("eval_count"))
        release(True)
        return content, _parse_calls(raw), raw

    # ---------- public ----------
    def respond(
        self,
        text: str,
        on_text: Callable[[str], None] = lambda s: None,
        on_segment_end: Callable[[], None] = lambda: None,
        on_status: Callable[[str], None] = lambda s: None,
        cancel: threading.Event | None = None,
    ) -> str:
        cancel = cancel or threading.Event()
        with self.lock:
            if cancel.is_set():
                return ""
            if not self.messages or time.time() - self._last_used > self.idle_seconds:
                self._new_session()
            self._trim()
            self._last_used = time.time()
            self.memory.log("user", text)
            self.messages.append({"role": "user", "content": f"[{datetime.now():%A %d %B %Y, %H:%M}]\n{text}\n{REMINDER}"})
            spoken: list[str] = []

            def emit(delta: str) -> None:
                if not cancel.is_set():
                    spoken.append(delta)
                    on_text(delta)

            try:
                if not self.ensure_server():
                    emit("My local language model is not running. Please install or start Ollama.")
                else:
                    self._loop(emit, on_segment_end, on_status, cancel)
            except LookupError:
                emit(f"The model {self.model} has not been downloaded yet. Run: ollama pull {self.model}.")
            except requests.RequestException as e:
                log.error("ollama request failed: %s", e)
                emit("I lost contact with my local language model.")
            except Exception:
                log.exception("unexpected error in local agent loop")
                emit("Something went wrong on my side. The details are in my log.")
                self.messages = []
            on_segment_end()
            self._last_used = time.time()
            said = "".join(spoken).strip()
            self.memory.log("assistant", said)
            return said

    def _loop(self, emit, on_segment_end, on_status, cancel) -> None:
        nudge = {"role": "user", "content": "You gave no answer. Call the right tool or reply in one sentence."}
        nudged = False
        turn_start = len(self.messages)
        failed: set[str] = set()

        def give_up(reason: str) -> None:
            """Stop a turn that is going nowhere, and forget its failed attempts so they cannot derail later turns."""
            emit(reason)
            del self.messages[turn_start:]
            self.messages.append({"role": "assistant", "content": reason})

        # A question about the live state of the PC or the world cannot be answered without a tool. If the
        # model tries, its made-up answer is not spoken and it is told once to call the tool.
        must_use_tool = bool(_LIVE.search(self.messages[-1]["content"].split("\n")[1]))
        push = {"role": "user", "content": "You answered without calling a tool, so the answer may be invented. Call the right tool now."}
        for step in range(self.max_steps):
            held: list[str] = []
            result = self._chat(held.append if must_use_tool and step == 0 else emit, cancel)
            if result is None:
                return
            content, calls, _raw = result
            if step == 1 and push in self.messages:
                self.messages.remove(push)
            if held and not calls:
                log.info("answered %r without a tool; asking again", content[:80])
                self.messages.append(push)
                continue
            for piece in held:
                emit(piece)
            if nudged:
                self.messages.remove(nudge)  # keep the history clean so it cannot confuse later turns
            if not content.strip() and not calls:
                # Small models now and then return nothing at all; ask once more rather than stay silent.
                if nudged:
                    sorry = "I did not manage to work that out. Please say it another way."
                    emit(sorry)
                    self.messages.append({"role": "assistant", "content": sorry})
                    return
                nudged = True
                self.messages.append(nudge)
                continue
            nudged = False
            on_segment_end()
            written = "".join(f"\n{_CALL_TAG}\n{json.dumps(c)}\n</tool_call>" for c in calls)
            self.messages.append({"role": "assistant", "content": (content + written).strip()})
            if not calls:
                return
            if content:
                emit(" ")  # keeps "Let me look." apart from the sentence that follows the tool call
            for call in calls:
                name, args = call["name"], call["arguments"]
                if cancel.is_set():
                    out = "Cancelled by the user before this ran."
                else:
                    tool = self.registry.tools.get(name)
                    if tool:
                        args = _coerce(args, tool.schema)
                    if name == "type_text" and re.search(r"need no tool|call the tool|tool_call|answer(ed)? without", str(args.get("text", "")), re.I):
                        # the model is typing its own instructions into the user's document; never let that through
                        give_up("Sorry, I did not catch that.")
                        return
                    on_status(name)
                    log.info("tool %s %s", name, args)
                    out, is_error = self.registry.run(name, args)
                    if isinstance(out, list):
                        out = " ".join(b.get("text", "") for b in out if b.get("type") == "text")
                    log.info("  -> %s", out[:300])
                    if is_error:
                        key = name + json.dumps(args, sort_keys=True, default=str)
                        if key in failed or len(failed) >= 3:  # repeating a failed call, or failing every way it tries
                            give_up(f"I could not do that. {out[:200]}")
                            return
                        failed.add(key)
                        out = "ERROR: " + out
                self.messages.append({"role": "user", "content": f"<tool_response>\n{out[:6000]}\n</tool_response>"})
            if cancel.is_set():
                return
        give_up("I have taken several steps without finishing, so I have stopped.")
