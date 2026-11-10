# J.A.R.V.I.S. — voice assistant for Windows

Say **"Hey Jarvis"**, speak a request, and it answers aloud and carries the task out on this PC.

```
 microphone ──► wake word ──► record until you pause ──► speech-to-text ──► Claude agent ──► voice
               (local, CPU)                              (local, RTX 3050)   │  ▲            (Edge neural,
                                                                             ▼  │             offline fallback)
                                                                        PC-control tools
                                                              apps · files · system · screen · shell · memory
```

| Part | What runs | Where |
|---|---|---|
| Wake word | openWakeWord `hey_jarvis` | local, CPU |
| Speech-to-text | faster-whisper `small.en` on the GPU, about 0.25 s per command (or `large-v3-turbo` when the brain is Claude) | local |
| Brain (default, free) | Ollama running `qwen2.5:3b` on the RTX 3050, with tool use and DuckDuckGo web search | local, no API key |
| Brain (optional) | Claude (`claude-opus-5-5`), far stronger at multi-step tasks and able to see the screen | cloud, paid API key |
| Voice | Edge neural voice `en-GB-RyanNeural` | cloud, falls back to offline Windows SAPI |
| Memory | SQLite: long-term facts, conversation log, reminders | local (`data/memory.db`) |

## Setup

1. `powershell -ExecutionPolicy Bypass -File setup.ps1` (creates `.venv`, installs everything, runs a self-test).
2. Brain, free and local (default): install Ollama from https://ollama.com/download, then run `ollama pull qwen2.5:3b`
   (about 1.9 GB, one time). No key, no cost, works offline.
   Or, for the cloud brain: set `brain.provider: claude` and `stt.device: auto` in `config.yaml`, and put
   `ANTHROPIC_API_KEY=sk-ant-...` in `.env`.
3. Make it an app: `powershell -ExecutionPolicy Bypass -File install_app.ps1` puts a **Jarvis** icon on the
   Desktop and in the Start menu (`-Remove` to undo). Opening it shows the Jarvis window: the animated ring
   (blue standby, green listening, amber thinking), system gauges, the conversation, a box to type requests,
   and TALK / MUTE / STOP / QUIT. Closing the window leaves Jarvis running in the system tray; opening the
   icon again, or "Show window" on the tray icon, brings it back.
   Other ways to start:
   - `Jarvis.bat` — the same, plus a console showing what it hears and does
   - `Jarvis (background).bat` — no console
   - `autostart.ps1` — start at every sign-in without opening the window (`-Remove` to undo)
   - `python main.py --no-ui` — no window at all; `ui.enabled: false` in `config.yaml` makes that permanent

Other modes: `python main.py --text` (type instead of talk, add `--speak` to hear replies),
`--check` (test every component), `--devices` (list microphones/speakers).

## Using it

- **"Hey Jarvis"** then your request. A rising chime means it is listening; it stops when you pause.
- **`Ctrl+Alt+J`** does the same as the wake word.
- Each new request needs the wake word (or TALK / the hotkey). When Jarvis asks you a question it listens for
  your answer by itself. `assistant.follow_up: true` makes it listen after every reply instead.
- Say **"Hey Jarvis"** while it is talking to interrupt; "stop" / "never mind" dismisses it.
- Risky actions (delete, overwrite, shell commands that change things, shutdown/restart, Wi-Fi off,
  force-kill) are read back and need a spoken **yes**. Turn this off in `config.yaml` → `safety`.

Things to try: "open Spotify and Chrome" · "set volume to 30 and dim the screen" · "what's on my screen?" ·
"find the PDF I downloaded yesterday and summarise it" · "create a folder called Invoices on the desktop and
move all the PDFs from Downloads into it" · "remind me in 20 minutes to check the oven" ·
"what's the weather in Dhaka tomorrow?" · "remember that my project files live in D:\AIRIL" ·
"type an email reply saying I'll be there at five".

## Instant commands and dictation

Requests that mean exactly one thing skip the language model, so they happen at once and exactly as said
(`jarvis/intents.py`; anything else, or anything that fails, goes to the model):

- **Typing:** "type hello world", "write see you at five and press enter" — types those exact words where the cursor is.
- **Keys:** "press enter", "press control shift S", "select all", "copy that", "paste", "undo", "save", "new line",
  "new tab", "close tab", "switch window", "scroll down", "go back", "refresh", "show desktop", "take a screenshot".
- **Apps and sites:** "open Notepad and Chrome", "open YouTube", "close the calculator".
- **Sound, screen, media:** "volume 30", "turn the volume up", "mute", "brightness 50", "pause", "next song".
- **Search:** "search for cheap flights", "search lofi music on YouTube". **Time:** "what time is it".
- **WhatsApp:** "send a WhatsApp message to Akash saying I am busy", "tell Akash on WhatsApp that I will be late",
  or just "send a WhatsApp message" and Jarvis asks who and what. It opens the WhatsApp desktop app, finds the
  chat by name (and stops if no chat matches), types the message, then asks "Shall I send…?" with the text
  visible in the chat. Say yes to send; anything else clears it.
- **Dictation mode:** "start dictation" — everything you say is typed until you say "stop dictation".
  While dictating: "new line", "new paragraph", "delete that" (removes the last thing typed).

Typing goes to whichever window has the keyboard focus, so click where you want the text first.

## What it can do (tools)

| Area | Tools |
|---|---|
| Apps & windows | `open_app`, `close_app`, `list_open_windows`, `window_action`, `find_installed_apps` |
| Files & documents | `list_folder`, `search_files`, `read_file` (text/PDF/Word), `write_file`, `create_folder`, `move_path`, `copy_path`, `delete_path` (Recycle Bin), `open_path` |
| System | `set_volume`, `set_brightness`, `set_radio` (Wi-Fi/Bluetooth), `power`, `media_control`, `system_status`, `open_settings` |
| Screen, keyboard, mouse | `look_at_screen`, `click`, `type_text`, `press_keys`, `scroll`, `get_clipboard`, `set_clipboard` |
| Web | `web_search` (answers), `get_weather` and `read_web_page` (local brain), `open_url`, `search_in_browser` |
| Maths | `calculate` |
| Anything else | `run_powershell` |
| Memory & time | `remember`, `forget`, `recall`, `set_reminder`, `list_reminders`, `cancel_reminder` |

## Adding a capability

Write one function in a module under `jarvis/tools/` (new modules go in `MODULES` in `jarvis/tools/__init__.py`):

```python
from . import S, tool

@tool("Say what the tool does and when to use it.", {"city": S("City name")})
def get_weather(city: str):
    return f"Sunny in {city}"
```

Pass `danger=lambda args: "description of the risky thing"` to require a spoken confirmation.

## Layout

```
main.py              entry point and modes
config.yaml          all settings (voice, model, wake-word sensitivity, safety...)
jarvis/assistant.py  voice loop and state machine
jarvis/ui.py  ui/index.html   the window (a local page in an Edge app window, token-protected, 127.0.0.1 only)
jarvis/brain.py      Claude agent loop, system prompt, session handling
jarvis/wake.py  stt.py  tts.py  audio.py     the voice pipeline
jarvis/memory.py     SQLite store
jarvis/tools/        everything it can do on the PC
```

## Tuning

- Mishears names of your apps, people or places → add them to `stt.hints` in `config.yaml`.
- Triggers by accident / misses you → `wake_word.threshold` (higher = stricter).
- Cuts you off mid-sentence → raise `audio.silence_seconds`. Never stops recording → set `audio.energy_threshold` to a number (try 500).
- Local brain makes mistakes on harder tasks → try `brain.local.model: qwen2.5:7b` (smarter, much slower: it only partly fits the GPU).
- Avoid models that always think before answering (the current `qwen3:4b`): measured here at 15 s to 2 min per command, against about 1 s for `qwen2.5:3b`.
- Local brain is slow → close GPU-heavy apps (browsers, games); it needs about 2.3 GB of free VRAM to stay fully on the GPU.
- With the local brain, `look_at_screen`/`click`/`scroll` are disabled (the model cannot see images) and web search uses DuckDuckGo.
- Claude brain: faster, cheaper replies → `brain.model: claude-sonnet-5-5` or `claude-haiku-5-5`. More careful multi-step work → `brain.effort: medium`.
- Another language → `stt.language: null` (auto-detect) or a code such as `bn`, and pick a matching `tts.voice`.
- Logs are in `logs/jarvis.log`.
