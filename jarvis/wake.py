"""Wake word ("Hey Jarvis") - runs locally on the CPU with openWakeWord."""
from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("jarvis.wake")


class WakeWord:
    def __init__(self, cfg: dict):
        import openwakeword
        from openwakeword.model import Model

        name = cfg["wake_word"]["model"]
        self.threshold = float(cfg["wake_word"]["threshold"])
        openwakeword.utils.download_models([name])  # no-op once the files are present
        self.model = Model(wakeword_models=[name], inference_framework="onnx")
        self._last = 0.0
        log.info("wake word ready: %s (threshold %.2f)", name, self.threshold)

    def process(self, frame: np.ndarray, strict: bool = False) -> bool:
        """Feed one 80 ms int16 frame; True when the wake word was just spoken.

        strict: used while Jarvis itself is talking, when its own voice reaches the microphone; only a
        clear "Hey Jarvis" counts then.
        """
        scores = self.model.predict(frame)
        score = max(scores.values()) if scores else 0.0
        if score >= (max(self.threshold, 0.85) if strict else self.threshold) and time.time() - self._last > 2.0:
            self._last = time.time()
            self.model.reset()
            log.info("wake word detected (score %.2f)", score)
            return True
        return False
