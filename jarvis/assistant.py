"""The voice loop: wake word -> record command -> transcribe -> think and act -> speak.

The main thread owns the microphone. Each request is handled on a worker thread so the wake word
keeps working while Jarvis is thinking or talking: say "Hey Jarvis" again to interrupt.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque

import numpy as np

from . import audio, intents
from .config import DATA_DIR
from .llm import create_brain, is_local
from .memory import Memory
from .stt import STT
from .tools import load_all
from .tools.timers import start_scheduler
from .tts import TTS
from .wake import WakeWord

log = logging.getLogger("jarvis")

_STOP_WORDS = re.compile(r"^\W*(stop|cancel|never ?mind|nothing|forget it|be quiet|quiet|shut up|that'?s all|no thanks?)\W*$", re.I)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|proceed|confirm|confirmed|please do|affirmative|correct|send it|send)\b", re.I)
_NO = re.compile(r"\b(no|nope|don'?t|do not|cancel|stop|negative|never ?mind|abort)\b", re.I)


class Assistant:
    def __init__(self, cfg: dict, on_stage=None):
        stage = on_stage or (lambda text: None)  # start-up progress, shown in the window
        self.cfg = cfg
        a = cfg["audio"]
        self.on_event = lambda kind, text="": None  # the window subscribes here: heard / say / tool / done
        self.level = 0.0  # current microphone loudness (int16 RMS), for the window's animation
        self._typed: queue.Queue[str] = queue.Queue()
        self._local = threading.local()  # per request thread: its cancel flag
        self.dictating = False  # dictation mode: type everything that is said
        self._dictated, self._dictation_idle = "", 0
        self.quit = threading.Event()
        self.muted = False
        self.status = "starting"
        self.memory = Memory(DATA_DIR / "memory.db")
        self.registry = load_all(local=is_local(cfg))
        self.registry.ctx.memory = self.memory
        self.registry.ctx.config = cfg
        self.registry.ctx.announce = self.announce
        self.registry.confirm_dangerous = cfg["safety"]["confirm_dangerous"]
        self.registry.confirm_handler = self._confirm
        self.tts = TTS(cfg)
        stage("Loading the language model")
        self.brain = create_brain(cfg, self.memory, self.registry)
        if hasattr(self.brain, "warm_up"):  # load the local model while the other parts start
            threading.Thread(target=self.brain.warm_up, daemon=True).start()
        from .tools import apps

        threading.Thread(target=apps._start_apps, daemon=True).start()  # so the first "open ..." is instant
        stage("Loading the wake word")
        self.wake = WakeWord(cfg)
        stage("Loading speech recognition")
        self.stt = STT(cfg)
        stage("Opening the microphone")
        self.mic = audio.Mic(a["input_device"])
        self._hotkey = threading.Event()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._followup = threading.Event()
        self._ask: dict | None = None  # pending question from a worker thread (confirmation)
        self._noise = 150.0  # running estimate of the room's background level (int16 RMS)

    # ---------- things other threads call ----------
    def announce(self, text: str) -> None:
        """Speak something unprompted (reminders)."""
        audio.chime("wake", self.cfg["audio"]["output_device"])
        time.sleep(0.25)
        self._say(text)

    def _say(self, text: str) -> None:
        self.on_event("say", text)
        self.on_event("done")
        self.tts.say(text)

    def submit_text(self, text: str) -> None:
        """A request typed in the window; handled exactly like a spoken one."""
        self._typed.put(text)

    def _confirm(self, what: str) -> bool:
        """Called on the worker thread by a risky tool: ask aloud, wait for a spoken yes."""
        hon = self.cfg["assistant"].get("honorific") or ""
        answer = self._question(f"Shall I {what}{', ' + hon if hon else ''}?", wait=8)
        log.info("confirmation for %r: heard %r", what, answer)
        ok = bool(_YES.search(answer)) and not _NO.search(answer)
        if not ok and not self._cancelled():
            self._say("Understood, leaving it.")
        return ok

    # ---------- recording ----------
    def _threshold(self) -> float:
        fixed = self.cfg["audio"]["energy_threshold"]
        if isinstance(fixed, (int, float)):
            return float(fixed)
        return float(min(max(self._noise * 3.0, 220.0), 4000.0))

    def _record(self, no_speech_timeout: float) -> np.ndarray | None:
        """Capture one utterance: waits for speech, stops after a pause. None if nobody spoke."""
        a = self.cfg["audio"]
        thr = self._threshold()
        end_frames = max(3, round(a["silence_seconds"] / audio.FRAME_SECONDS))
        max_frames = round(a["max_command_seconds"] / audio.FRAME_SECONDS)
        pre: deque[np.ndarray] = deque(maxlen=6)
        frames: list[np.ndarray] = []
        loud_run, quiet_run, started = 0, 0, False
        t0 = time.time()
        while not self.quit.is_set():
            frame = self.mic.read(0.3)
            if frame is None:
                continue
            self.level = audio.rms(frame)
            loud = self.level > thr
            if not started:
                pre.append(frame)
                loud_run = loud_run + 1 if loud else 0
                if loud_run >= 3:  # ~240 ms of sustained sound, so the chime or a click doesn't count
                    started = True
                    frames = list(pre)
                elif time.time() - t0 > no_speech_timeout:
                    return None
                continue
            frames.append(frame)
            quiet_run = 0 if loud else quiet_run + 1
            if quiet_run >= end_frames or len(frames) >= max_frames:
                break
        return np.concatenate(frames) if frames else None

    def _listen(self, no_speech_timeout: float, chime: bool = True) -> str:
        self.status = "listening"
        if chime:
            audio.chime("wake", self.cfg["audio"]["output_device"])
        clip = self._record(no_speech_timeout)
        if clip is None:
            return ""
        self.status = "transcribing"
        t0 = time.time()
        text = self.stt.transcribe(clip)
        log.info("heard (%.1fs audio, %.2fs to transcribe): %r", len(clip) / audio.RATE, time.time() - t0, text)
        if text:
            self.on_event("heard", text)
        return text

    # ---------- handling a request ----------
    def _start_request(self, text: str) -> None:
        self._cancel = cancel = threading.Event()

        said: list[str] = []

        def on_text(delta: str) -> None:
            if not cancel.is_set():
                said.append(delta)
                self.on_event("say", delta)
                self.tts.feed(delta)

        def on_status(tool: str) -> None:
            log.info("... %s", tool)
            self.on_event("tool", tool)

        def work():
            self._local.cancel = cancel
            self.status = "thinking"
            intent = intents.match(text)
            if intent is None or not self._instant(intent, text, on_status):
                self.brain.respond(
                    text,
                    on_text=on_text,
                    on_segment_end=lambda: None if cancel.is_set() else self.tts.flush(),
                    on_status=on_status,
                    cancel=cancel,
                )
            self.on_event("done")
            if cancel.is_set():
                return
            self.tts.wait(180)
            if not cancel.is_set():
                self.status = "idle"
                # Listening again without the wake word is convenient, but a video or people talking nearby
                # then get treated as commands. By default do it only when Jarvis has just asked something.
                mode = self.cfg["assistant"]["follow_up"]
                if mode is True or (mode == "questions" and "".join(said).rstrip().endswith("?")):
                    self._followup.set()

        self._worker = threading.Thread(target=work, name="request", daemon=True)
        self._worker.start()

    def _instant(self, intent: intents.Intent, text: str, on_status) -> bool:
        """Carry out an instant command. False means it did not work and the language model should try."""
        if intent.special == "whatsapp":
            return self._whatsapp(intent.data, text, on_status)
        if intent.special == "dictation_on":
            self.dictating = True
            self._say("Dictation on. Say stop dictation when you are finished.")
            return True
        reply = intent.say or ""
        for i, (name, args) in enumerate(intent.actions):
            on_status(name)
            out, is_error = self.registry.run(name, args)
            log.info("instant %s %s -> %s", name, args, str(out)[:200])
            if is_error:
                if i == 0:
                    return False
                reply = f"{reply} {out}".strip()
                break
            if not intent.silent and isinstance(out, str):
                reply = f"{reply} {out.splitlines()[0]}".strip()
        self.memory.log("user", text)
        self.memory.log("assistant", reply or "(done)")
        if reply:
            self._say(reply)
        else:
            audio.chime("done", self.cfg["audio"]["output_device"])
        return True

    def _question(self, prompt: str, wait: float = 12) -> str:
        """Ask the user something aloud and return what they answer ('' if nothing was said)."""
        if self._cancelled():
            return ""
        self.tts.flush()
        self._say(prompt)
        self.tts.wait(30)
        if self._cancelled():  # the user interrupted with a new request while the question was being spoken
            return ""
        req = {"done": threading.Event(), "answer": "", "timeout": wait}
        self._ask = req
        req["done"].wait(wait + 25)
        self._ask = None
        return req["answer"].strip()

    def _cancelled(self) -> bool:
        cancel = getattr(self._local, "cancel", None)
        return cancel is not None and cancel.is_set()

    def _whatsapp(self, data: dict, text: str, on_status) -> bool:
        """Send a WhatsApp message, asking for whatever part of the request was left out."""
        contact, message = data.get("contact"), data.get("message")
        if not contact:
            contact = self._question("Who should I send the message to?").strip(" .!?")
            contact = re.sub(r"^(to|send (it )?to|it'?s for|for|message) ", "", contact, flags=re.I).strip()
        if not contact or _STOP_WORDS.match(contact):
            self._say("All right, no message sent.")
            return True
        if not message:
            message = self._question(f"What should I say to {contact}?")
            message = re.sub(r"^(say|tell (him|her|them)|write|type|that)( that)? ", "", message, flags=re.I).strip()
        if not message or _STOP_WORDS.match(message):
            self._say("All right, no message sent.")
            return True
        on_status("send_whatsapp")
        out, is_error = self.registry.run("send_whatsapp", {"contact": contact, "message": message})
        log.info("instant send_whatsapp %r %r -> %s", contact, message, out)
        self.memory.log("user", text)
        self.memory.log("assistant", str(out))
        if is_error:
            self._say(re.sub(r"^\w+Error: ", "", str(out)).split(" Ask the user")[0])
        elif str(out).startswith("Sent"):
            self._say(str(out).split(":")[0] + ".")  # a declined message was already acknowledged by the confirmation
        return True

    def _on_dictation(self) -> None:
        """Dictation mode: everything said is typed into the focused window until "stop dictation"."""
        text = self._listen(no_speech_timeout=12, chime=False)
        if not text:
            self._dictation_idle += 1
            if self._dictation_idle >= 4:  # about a minute of silence
                self.dictating = False
                self._say("Dictation off.")
            self.status = "idle"
            return
        self._dictation_idle = 0
        command = re.sub(r"[^a-z ]", "", text.lower()).strip()
        command = re.sub(r"^(hey |ok |okay )?(jarvis )?(please )?", "", command)
        if re.fullmatch(r"(stop|end|exit|finish|close|turn off) (the )?(dictation|dictating|typing|writing)( mode)?|dictation off", command):
            self.dictating = False
            self._say("Dictation off.")
        elif command in ("new line", "next line", "press enter", "enter"):
            self.registry.run("press_keys", {"keys": ["enter"]})
        elif command == "new paragraph":
            self.registry.run("press_keys", {"keys": ["enter", "enter"]})
        elif command in ("delete that", "scratch that", "undo that", "remove that"):
            import pyautogui

            pyautogui.press("backspace", presses=min(len(self._dictated), 600), interval=0.004)
            self._dictated = ""
        else:
            self._dictated = text + " "
            self.registry.run("type_text", {"text": self._dictated})
        self.status = "idle"

    def _interrupt(self) -> None:
        self._cancel.set()
        self._followup.clear()
        self.tts.stop()

    def _on_wake(self) -> None:
        was_speaking = self.tts.speaking
        self._interrupt()
        if was_speaking:  # let Jarvis's own voice die away so it is not recorded as the command
            time.sleep(0.4)
            self.mic.drain()
        text = self._listen(no_speech_timeout=6)
        if not text:
            self.status = "idle"
            return
        if _STOP_WORDS.match(text):
            audio.chime("done", self.cfg["audio"]["output_device"])
            self.status = "idle"
            return
        self._start_request(text)

    def _on_followup(self) -> None:
        """After answering, listen briefly so the user can reply without the wake word."""
        self._followup.clear()
        time.sleep(0.35)  # let the room go quiet after our own voice
        self.mic.drain()
        text = self._listen(no_speech_timeout=self.cfg["assistant"]["follow_up_seconds"], chime=False)
        if text and not _STOP_WORDS.match(text) and len(text) > 2:
            self._start_request(text)
        else:
            self.status = "idle"

    def _on_ask(self, req: dict) -> None:
        self.mic.drain()
        req["answer"] = self._listen(no_speech_timeout=req.get("timeout", 8))
        req["done"].set()
        self.status = "thinking"

    # ---------- main loop ----------
    def run(self) -> None:
        hotkey = self.cfg["assistant"].get("hotkey")
        if hotkey:
            try:
                import keyboard

                keyboard.add_hotkey(hotkey, self._hotkey.set)
            except Exception as e:
                log.warning("hotkey %s unavailable: %s", hotkey, e)
        stop_sched = threading.Event()
        start_scheduler(stop_sched)
        self.mic.start()
        self.status = "idle"
        name = self.cfg["assistant"]["name"]
        log.info('%s is online. Say "Hey Jarvis"%s.', name, f" or press {hotkey}" if hotkey else "")
        hon = self.cfg["assistant"].get("honorific") or ""
        self._say(f"{name} online{', ' + hon if hon else ''}.")
        try:
            while not self.quit.is_set():
                if not self._typed.empty() and self._ask is None:
                    typed = self._typed.get()
                    self._interrupt()
                    self.on_event("heard", typed)
                    self._start_request(typed)
                    continue
                req = self._ask
                if req is not None and not req["done"].is_set():
                    self._on_ask(req)
                    continue
                if self.dictating and not self.muted:
                    self._followup.clear()
                    if self.tts.speaking:
                        time.sleep(0.1)
                    else:
                        self._on_dictation()
                    continue
                if self._followup.is_set() and not self.tts.speaking:
                    self._on_followup()
                    continue
                frame = self.mic.read(0.2)
                if frame is None:
                    continue
                self.level = level = audio.rms(frame)
                if self.muted:
                    self._hotkey.clear()
                    continue
                if not self.tts.speaking:  # learn the room's noise floor only while we are silent
                    # follow quiet quickly, loud slowly, so speech in the room doesn't inflate the estimate
                    k = 0.1 if level < self._noise else 0.004
                    self._noise = (1 - k) * self._noise + k * min(level, 2000.0)
                if self.wake.process(frame, strict=self.tts.speaking) or self._hotkey.is_set():
                    self._hotkey.clear()
                    self._on_wake()
        finally:
            stop_sched.set()
            self._interrupt()
            self.mic.stop()
            log.info("%s offline.", name)

    def shutdown(self) -> None:
        self.quit.set()
