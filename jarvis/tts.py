"""Text-to-speech with streaming and instant interruption.

Text arrives in fragments as the model writes; each finished sentence is synthesised and played
while the next is still being generated. Voice: Microsoft Edge neural voices (cloud, free);
if that is unreachable, the offline Windows SAPI voice.
"""
from __future__ import annotations

import asyncio
import io
import logging
import queue
import re
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger("jarvis.tts")

_SENTENCE_END = re.compile(r"(?<=[.!?:;])[\"')\]]?\s+|\n+")
_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*_`#>|~]+|^\s*[-•]\s+", re.MULTILINE)


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = _URL.sub("a link", text)
    text = _MARKUP.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


class TTS:
    def __init__(self, cfg: dict):
        c = cfg["tts"]
        self.voice, self.rate, self.pitch = c["voice"], c["rate"], c["pitch"]
        self.device = cfg["audio"]["output_device"]
        self._buf = ""
        self._gen = 0  # bumped by stop(); stale queue items are dropped
        self._pending = 0
        self._cond = threading.Condition()
        self._text_q: queue.Queue[tuple[int, str]] = queue.Queue()
        self._audio_q: queue.Queue[tuple[int, np.ndarray, int]] = queue.Queue()
        self._edge_down_until = 0.0
        threading.Thread(target=self._synth_loop, name="tts-synth", daemon=True).start()
        threading.Thread(target=self._play_loop, name="tts-play", daemon=True).start()

    # ---------- public ----------
    def feed(self, delta: str) -> None:
        """Add streamed text; complete sentences start speaking immediately."""
        self._buf += delta
        while True:
            m = _SENTENCE_END.search(self._buf)
            if not m or (m.end() < 12 and len(self._buf) < 40):  # avoid choppy 1-2 word fragments
                break
            self._enqueue(self._buf[: m.end()])
            self._buf = self._buf[m.end():]

    def flush(self) -> None:
        self._enqueue(self._buf)
        self._buf = ""

    def say(self, text: str) -> None:
        self.feed(text)
        self.flush()

    def stop(self) -> None:
        """Stop speaking right now and discard anything queued."""
        with self._cond:
            self._gen += 1
            self._buf = ""
            for q in (self._text_q, self._audio_q):
                while True:
                    try:
                        q.get_nowait()
                        self._pending -= 1
                    except queue.Empty:
                        break
            self._cond.notify_all()

    @property
    def speaking(self) -> bool:
        return self._pending > 0

    def wait(self, timeout: float | None = None) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._pending <= 0, timeout)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Text -> (mono float32 samples, sample rate)."""
        if time.time() >= self._edge_down_until:
            try:
                return self._edge(text)
            except Exception as e:
                log.warning("Edge voice unavailable (%s); using the offline Windows voice for a minute", e)
                self._edge_down_until = time.time() + 60
        return self._sapi(text)

    # ---------- internals ----------
    def _enqueue(self, text: str) -> None:
        text = clean_for_speech(text)
        if not re.search(r"\w", text):
            return
        with self._cond:
            self._pending += 1
            self._text_q.put((self._gen, text))

    def _done_one(self) -> None:
        with self._cond:
            self._pending = max(0, self._pending - 1)
            self._cond.notify_all()

    def _edge(self, text: str) -> tuple[np.ndarray, int]:
        import edge_tts

        async def run() -> bytes:
            out = bytearray()
            async for chunk in edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch).stream():
                if chunk["type"] == "audio":
                    out += chunk["data"]
            return bytes(out)

        mp3 = asyncio.run(asyncio.wait_for(run(), timeout=15))
        if not mp3:
            raise RuntimeError("no audio returned")
        data, sr = sf.read(io.BytesIO(mp3), dtype="float32", always_2d=True)
        return data.mean(axis=1), sr

    def _sapi(self, text: str) -> tuple[np.ndarray, int]:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        path = Path(tempfile.gettempdir()) / f"jarvis_tts_{threading.get_ident()}.wav"
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(str(path), 3)
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        path.unlink(missing_ok=True)
        return data.mean(axis=1), sr

    def _synth_loop(self) -> None:
        while True:
            gen, text = self._text_q.get()
            if gen != self._gen:
                self._done_one()
                continue
            try:
                audio, sr = self.synthesize(text)
            except Exception:
                log.exception("speech synthesis failed")
                self._done_one()
                continue
            with self._cond:
                if gen != self._gen:  # stopped while synthesising (stop() could not see this item)
                    self._pending = max(0, self._pending - 1)
                    self._cond.notify_all()
                    continue
                self._audio_q.put((gen, audio, sr))

    def _play_loop(self) -> None:
        while True:
            gen, audio, sr = self._audio_q.get()
            try:
                if gen == self._gen:
                    with sd.OutputStream(samplerate=sr, channels=1, dtype="float32", device=self.device) as out:
                        step = sr // 20  # 50 ms slices so stop() takes effect at once
                        for i in range(0, len(audio), step):
                            if gen != self._gen:
                                break
                            out.write(audio[i:i + step].reshape(-1, 1))
            except Exception:
                log.exception("audio playback failed")
            finally:
                self._done_one()
