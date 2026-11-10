"""Speech-to-text - runs locally with faster-whisper, on the GPU when its CUDA libraries are usable."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import numpy as np

from .config import MODELS_DIR

log = logging.getLogger("jarvis.stt")


def _cuda_ready() -> bool:
    """Expose pip-installed CUDA DLLs to the loader and confirm they are all present.

    A missing cuDNN/cuBLAS DLL kills the process outright instead of raising, so check up front.
    """
    nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs = [d for d in nvidia.glob("*/bin") if d.is_dir()] if nvidia.exists() else []
    for d in dirs:
        os.add_dll_directory(str(d))
        os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]
    search = [Path(p) for p in os.environ["PATH"].split(os.pathsep) if p]
    for dll in ("cublas64_12.dll", "cudnn_ops64_9.dll"):
        if not any((p / dll).exists() for p in search):
            log.info("GPU speech recognition unavailable: %s not found", dll)
            return False
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class STT:
    def __init__(self, cfg: dict):
        from faster_whisper import WhisperModel

        c = cfg["stt"]
        self.language = c["language"]
        self.cpu_model = c["cpu_model"]
        self.hints = (c.get("hints") or "").strip() or None
        want = c["device"]
        self.model = None
        if want in ("auto", "cuda") and _cuda_ready():
            try:
                # int8_float16: small.en takes about 0.35 GB of VRAM, large-v3-turbo about 1.5 GB
                self.model = WhisperModel(c["model"], device="cuda", compute_type="int8_float16", download_root=str(MODELS_DIR))
                self.model.transcribe(np.zeros(8000, dtype=np.float32), language="en")  # force the CUDA libs to load now
                self.device, self.name = "cuda", c["model"]
            except Exception as e:
                log.warning("GPU speech recognition failed (%s); using the CPU", e)
                self.model = None
        if self.model is None:
            self.model = WhisperModel(c["cpu_model"], device="cpu", compute_type="int8", cpu_threads=max(4, (os.cpu_count() or 8) // 2), download_root=str(MODELS_DIR))
            self.device, self.name = "cpu", c["cpu_model"]
        log.info("speech-to-text ready: %s on %s", self.name, self.device)

    def _run(self, audio: np.ndarray) -> str:
        lang = self.language
        if lang and self.name.endswith(".en"):
            lang = "en"
        segments, _ = self.model.transcribe(
            audio,
            language=lang,
            beam_size=5 if self.device == "cuda" else 1,  # greedy decoding is about twice as fast on the CPU
            initial_prompt=self.hints,  # the words to expect: app names, command verbs, the user's own vocabulary
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 250},
            condition_on_previous_text=False,
            no_repeat_ngram_size=3,
        )
        parts = [s.text.strip() for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        return " ".join(p for p in parts if p).strip()

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: 16 kHz mono, int16 or float32."""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32) / 32768.0
        audio = audio - float(audio.mean())  # remove the microphone's DC offset
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        if 0.003 < peak < 0.5:  # quiet speech is recognised much better once brought up to a normal level
            audio = audio * min(0.7 / peak, 12.0)
        try:
            text = self._run(audio)
        except Exception as e:
            if self.device != "cuda":
                raise
            # Usually the GPU ran out of memory; carry on with the CPU for the rest of this session.
            log.warning("GPU speech recognition failed (%s); switching to the CPU", e)
            from faster_whisper import WhisperModel

            self.model = WhisperModel(self.cpu_model, device="cpu", compute_type="int8", cpu_threads=max(4, (os.cpu_count() or 8) // 2), download_root=str(MODELS_DIR))
            self.device, self.name = "cpu", self.cpu_model
            text = self._run(audio)
        if self.hints and len(text) > 40 and text.strip(" .").lower() in self.hints.lower():
            return ""  # on near-silence the recogniser can echo its hint text back
        return text
