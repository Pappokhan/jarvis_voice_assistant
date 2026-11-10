"""J.A.R.V.I.S. entry point.

  python main.py            voice mode: say "Hey Jarvis" (or press the hotkey)
  python main.py --text     type instead of talking (add --speak to hear the replies)
  python main.py --check    test every component and report what works
  python main.py --devices  list microphones and speakers
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

from jarvis.config import DATA_DIR, LOG_DIR, load_config


def setup_logging(verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(LOG_DIR / "jarvis.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # absent under pythonw (background mode)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "faster_whisper", "comtypes", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for very_noisy in ("screen_brightness_control", "huggingface_hub"):
        logging.getLogger(very_noisy).setLevel(logging.ERROR)


def have_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


NO_KEY = (
    "No Anthropic API key found. Create one at https://platform.claude.com/settings/keys and put it in\n"
    "the .env file next to main.py as:  ANTHROPIC_API_KEY=sk-ant-..."
)


def single_instance():
    import win32api
    import win32event
    import winerror

    handle = win32event.CreateMutex(None, False, "Local\\JarvisAssistantSingleton")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        from jarvis.ui import open_running_window

        shown = open_running_window()  # starting the app twice just brings its window back
        print("Jarvis is already running" + ("; its window has been opened." if shown else " (see the tray icon)."))
        sys.exit(0)
    return handle


def run_text(cfg: dict, speak: bool) -> None:
    from jarvis.llm import create_brain, is_local
    from jarvis.memory import Memory
    from jarvis.tools import load_all
    from jarvis.tools.timers import start_scheduler
    import threading

    memory = Memory(DATA_DIR / "memory.db")
    reg = load_all(local=is_local(cfg))
    reg.ctx.memory, reg.ctx.config = memory, cfg
    reg.ctx.announce = lambda t: print(f"\n[reminder] {t}")
    reg.confirm_dangerous = cfg["safety"]["confirm_dangerous"]
    reg.confirm_handler = lambda what: input(f"\n  Confirm: {what}? [y/N] ").strip().lower().startswith("y")
    tts = None
    if speak:
        from jarvis.tts import TTS

        tts = TTS(cfg)
    brain = create_brain(cfg, memory, reg)
    start_scheduler(threading.Event())
    print(f"{cfg['assistant']['name']} (text mode, {brain.model}). Type a request; 'exit' to quit.\n")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit"):
            break
        if not text:
            continue
        print("Jarvis: ", end="", flush=True)

        def on_text(d: str) -> None:
            print(d, end="", flush=True)
            if tts:
                tts.feed(d)

        brain.respond(
            text,
            on_text=on_text,
            on_segment_end=lambda: tts.flush() if tts else None,
            on_status=lambda s: print(f"\n  [{s}] ", end="", flush=True),
        )
        print("\n")
        if tts:
            tts.wait(120)


def run_check(cfg: dict) -> int:
    """Exercise every component without needing a person at the microphone."""
    import numpy as np

    failures = 0

    def step(name, fn):
        nonlocal failures
        t0 = time.time()
        try:
            detail = fn()
            print(f"  [ OK ] {name}: {detail}  ({time.time() - t0:.1f}s)")
        except Exception as e:
            failures += 1
            print(f"  [FAIL] {name}: {type(e).__name__}: {e}")

    print("Checking components...")
    state: dict = {}

    def mic():
        import sounddevice as sd
        from jarvis import audio

        m = audio.Mic(cfg["audio"]["input_device"])
        m.start()
        frames = [f for f in (m.read(0.5) for _ in range(20)) if f is not None][8:]  # skip the switch-on pop
        m.stop()
        if not frames:
            raise RuntimeError("no audio frames arrived")
        dev = sd.query_devices(cfg["audio"]["input_device"], "input")["name"]
        return f"{dev} (room level {np.mean([audio.rms(f) for f in frames]):.0f})"

    def tts():
        from jarvis.tts import TTS

        state["tts"] = t = TTS(cfg)
        samples, sr = t.synthesize("Hey Jarvis, open the calculator and set the volume to forty percent.")
        state["speech"] = (samples, sr)
        voice = "offline Windows voice (Edge voice unreachable)" if t._edge_down_until else cfg["tts"]["voice"]
        return f"{len(samples) / sr:.1f}s of speech from {voice}"

    def to16k():
        samples, sr = state["speech"]
        n = int(len(samples) * 16000 / sr)
        return np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples).astype(np.float32)

    def wake():
        from jarvis.wake import WakeWord

        w = WakeWord(cfg)
        pcm = (np.concatenate([np.zeros(16000, np.float32), to16k(), np.zeros(8000, np.float32)]) * 32767).astype(np.int16)
        best = 0.0
        for i in range(0, len(pcm) - 1280, 1280):
            scores = w.model.predict(pcm[i:i + 1280])
            best = max(best, max(scores.values()))
        if best < w.threshold:
            raise RuntimeError(f"did not trigger on a synthetic 'Hey Jarvis' (best score {best:.2f})")
        return f"triggered on a synthetic 'Hey Jarvis' (score {best:.2f})"

    def stt():
        from jarvis.stt import STT

        s = STT(cfg)
        t0 = time.time()
        text = s.transcribe(to16k())
        if "calculator" not in text.lower():
            raise RuntimeError(f"unexpected transcript: {text!r}")
        return f"{s.name} on {s.device} heard {text!r} in {time.time() - t0:.2f}s"

    def tools():
        from jarvis.memory import Memory
        from jarvis.tools import load_all

        reg = load_all(local=cfg["brain"]["provider"] != "claude")
        reg.ctx.memory = Memory(DATA_DIR / "memory.db")
        results = []
        for name, args in (("system_status", {}), ("list_open_windows", {}), ("set_volume", {}), ("set_brightness", {}),
                           ("list_folder", {"path": "desktop"}), ("find_installed_apps", {"query": "calc"}),
                           ("set_radio", {"radio": "wifi"}), ("list_reminders", {})):
            out, err = reg.run(name, args)
            if err:
                raise RuntimeError(f"{name}: {out}")
            results.append(name)
        return f"{len(reg.tools)} tools registered; ran {', '.join(results)}"

    def brain():
        if cfg["brain"]["provider"] != "claude":
            import requests

            loc = cfg["brain"]["local"]
            try:
                tags = requests.get(loc["url"] + "/api/tags", timeout=3).json()
            except requests.RequestException:
                raise RuntimeError("Ollama is not running or not installed (https://ollama.com/download)")
            names = [m["name"] for m in tags.get("models", [])]
            if not any(n == loc["model"] or n.startswith(loc["model"] + ":") for n in names):
                raise RuntimeError(f"model not downloaded yet - run: ollama pull {loc['model']}  (have: {names})")
            r = requests.post(loc["url"] + "/api/chat", timeout=300, json={
                "model": loc["model"], "stream": False, "options": {"num_ctx": loc["num_ctx"]},
                "messages": [{"role": "user", "content": "Reply with exactly one word: online"}]}).json()
            speed = r.get("eval_count", 0) / max(r.get("eval_duration", 1) / 1e9, 1e-6)
            return f"local {loc['model']} replied {r['message']['content'].strip()[:40]!r} at {speed:.0f} tokens/s"
        if not have_credentials():
            raise RuntimeError("no ANTHROPIC_API_KEY in .env yet")
        import anthropic

        client = anthropic.Anthropic()
        r = client.messages.create(model=cfg["brain"]["model"], max_tokens=200, output_config={"effort": "low"},
                                   messages=[{"role": "user", "content": "Reply with exactly: online"}])
        return f"{cfg['brain']['model']} replied {next(b.text for b in r.content if b.type == 'text')!r}"

    step("microphone", mic)
    step("voice (text-to-speech)", tts)
    if "speech" in state:
        step("wake word", wake)
        step("speech-to-text", stt)
    step("PC control tools", tools)
    step(f"brain ({cfg['brain']['provider']})", brain)
    print("All good." if not failures else f"{failures} component(s) need attention.")
    return failures


def main() -> None:
    ap = argparse.ArgumentParser(description="J.A.R.V.I.S. voice assistant")
    ap.add_argument("--text", action="store_true", help="type instead of talking")
    ap.add_argument("--speak", action="store_true", help="with --text: also speak the replies")
    ap.add_argument("--check", action="store_true", help="test every component")
    ap.add_argument("--devices", action="store_true", help="list audio devices")
    ap.add_argument("--no-tray", action="store_true", help="no system-tray icon")
    ap.add_argument("--no-ui", action="store_true", help="no window at all")
    ap.add_argument("--hidden", action="store_true", help="start without opening the window (open it from the tray)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    setup_logging(args.verbose)

    if args.devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    if args.check:
        sys.exit(run_check(cfg))
    if cfg["brain"]["provider"] == "claude" and not have_credentials():
        print(NO_KEY)
        logging.getLogger("jarvis").error("no Anthropic API key configured")
        if sys.stdout is None or not args.text:  # background/voice mode: say it aloud too
            try:
                from jarvis.tts import TTS

                t = TTS(cfg)
                t.say("I have no Anthropic API key yet. Please add one to the dot env file and start me again.")
                t.wait(30)
            except Exception:
                pass
        sys.exit(1)
    if args.text:
        run_text(cfg, args.speak)
        return

    _mutex = single_instance()
    ui = None
    if cfg["ui"]["enabled"] and not args.no_ui:
        try:
            from jarvis.ui import UI, open_window

            ui = UI(cfg)  # up before the models load, so the window can show the start-up progress
            if not args.hidden:
                open_window(ui.port)
        except Exception:
            logging.getLogger("jarvis").exception("the window could not be started; continuing without it")
            ui = None
    from jarvis.assistant import Assistant

    assistant = Assistant(cfg, on_stage=(lambda s: setattr(ui, "boot", s)) if ui else None)
    if ui:
        ui.attach(assistant)
    if not args.no_tray:
        from jarvis.tray import start_tray

        start_tray(assistant, ui)
    try:
        assistant.run()
    except KeyboardInterrupt:
        assistant.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Without a console (pythonw) a crash would otherwise vanish; put it in the log and stop every thread.
        logging.getLogger("jarvis").exception("Jarvis stopped because of an error")
        os._exit(1)
