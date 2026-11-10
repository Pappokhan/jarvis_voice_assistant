"""Microphone capture and UI chimes."""
from __future__ import annotations

import logging
import queue

import numpy as np
import sounddevice as sd

log = logging.getLogger("jarvis.audio")

RATE = 16000
FRAME = 1280  # 80 ms: the frame size the wake-word model expects
FRAME_SECONDS = FRAME / RATE


class Mic:
    """Continuously captures 16 kHz mono int16 frames into a queue."""

    def __init__(self, device=None):
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=400)
        self._stream = sd.InputStream(
            samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, device=device, callback=self._callback
        )

    def _callback(self, indata, frames, time_info, status):
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:  # nobody is consuming; drop the oldest audio
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()

    def read(self, timeout: float = 0.2) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self):
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def _tone(freq: float, ms: int, volume: float = 0.18, rate: int = 24000) -> np.ndarray:
    t = np.arange(int(rate * ms / 1000)) / rate
    wave = np.sin(2 * np.pi * freq * t) * volume
    fade = min(len(t) // 4, 300)
    wave[:fade] *= np.linspace(0, 1, fade)
    wave[-fade:] *= np.linspace(1, 0, fade)
    return wave.astype(np.float32)


_CHIMES = {
    "wake": np.concatenate([_tone(880, 70), _tone(1320, 90)]),
    "done": np.concatenate([_tone(1320, 70), _tone(880, 90)]),
    "error": _tone(330, 220),
}


def chime(kind: str, device=None) -> None:
    try:
        sd.play(_CHIMES[kind], 24000, device=device)
    except Exception:
        log.debug("chime failed", exc_info=True)
