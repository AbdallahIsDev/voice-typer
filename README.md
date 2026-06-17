# Voice Typer

Premium background voice-to-text for Windows. Press a hotkey, talk, stop — text is transcribed locally, cleaned, and auto-pasted into whatever app you're using. Runs in the system tray with a full Electron dashboard.

## How It Works

1. Starts in the system tray — tray icon appears in <200ms, model loads in background
2. Press the hotkey (default F4) anywhere to start recording
3. Talk freely — switch apps, browse, whatever
4. Press hotkey again to stop (or let silence/max-duration auto-stop)
5. Audio is transcribed locally: Whisper (faster-whisper), Qwen3-ASR, or NVIDIA Parakeet TDT v3
6. Text goes through a 9-step cleanup pipeline (dedup, misspellings, self-corrections, capitalization, auto-punctuation)
7. Custom vocabulary substitutions and voice template expansion are applied
8. Optional LLM polishing via OpenAI-compatible API with 4 presets
9. Text is copied to clipboard and auto-pasted into the focused field
10. Transcribed text is saved to searchable history (SQLite)

No cloud required. Fully offline after first model download.

## Features

### Recording & Audio
- **Global hotkey:** F2–F12 with composite modifier support (`<ctrl>+<alt>+f2`, etc.)
- **Modes:** Toggle (press-on/press-off) or push-to-talk (hold to record)
- **Streaming:** Chunk-by-chunk real-time transcription
- **Silence detection:** Auto-stop configurable timeout with exponential-backoff warnings
- **Max duration:** Hard cutoff prevents runaway recordings
- **Mic handling:** Device hot-plug detection, automatic fallback chain across host APIs
- **Audio quality monitoring:** Clipping detection, low volume warnings, noise floor alerts
- **Cancel:** ESC key during recording discards the session
- **Crash recovery buffer:** Unsaved text survives unexpected shutdowns

### ASR Backends
- **faster-whisper** (tiny.en / small.en / medium.en) — default, fast CPU inference
- **Qwen3-ASR-0.6B** — alternative transformer-based engine
- **NVIDIA Parakeet TDT v3** — NVIDIA's speech recognition model
- **Cloud providers:** OpenAI Whisper, Groq Whisper, Deepgram nova-2
- **GPU→CPU fallback:** 4-level automatic fallback (configured device → CPU/int8 → CPU/int8 tiny.en → CPU/float32 tiny.en)
- **Auto-setup:** GPU detection, dependency checks, pip install, model weight download via huggingface_hub

### Post-Processing
- **9-step text cleanup:** Duplicate removal, whisper hallucination cleanup, misspelling correction, phrase substitution, self-correction cleanup, sentence capitalization, pronoun-I capitalization, auto-punctuation
- **Custom vocabulary:** 6 categories (custom, medical, legal, technical, academic, personal) with import/export
- **Voice templates:** Trigger→expand with variable interpolation (`{today}`, `{now}`, `{clipboard}`, `{username}`)
- **LLM polishing:** OpenAI-compatible API with 4 presets (professional / casual / email / code)
- **Language support:** 25 languages + auto-detect

### History & Data
- **SQLite history:** Every transcription saved with timestamp and duration
- **Search & favorites:** Full-text search, star/favorite marking, pagination
- **Export:** JSON and CSV format export
- **Clear all:** Bulk delete with confirmation

### Window Management
- **Dashboard:** Frameless Electron window (1000×700), close-to-tray behavior
- **Waveform bubble:** Always-on-top frameless overlay (220×60), SVG animation, draggable, shows live RMS/peak levels during recording
- **Single-instance guard:** Only one instance runs at a time
- **Hidden startup mode:** Starts silently in the system tray

### Dashboard Pages
- **Home:** Microphone control, recording stats, quick actions
- **History:** Paginated transcription history with search, favorites, export
- **Templates:** Manage trigger-expand voice templates with variable support
- **Vocabulary:** 6-category vocabulary editor with import/export
- **Models:** ASR model selection, cloud API key management, test connection
- **Microphone:** Device list, live level meter, selection
- **Analytics:** Daily usage stats, 7-day activity chart, streaks
- **Settings:** 7 sections, 30+ settings (see below)

### System Tray
- **Dynamic icon:** State-aware colors (idle, recording, processing)
- **Context menu:** Toggle Dictation → Open Dashboard → Models → Restart → Quit
- **Configurable click:** Left-click action (open dashboard or toggle dictation)
- **Notifications:** Desktop alerts with safety-alert bypass

### Theme & UI
- **Theme:** Dark / light / system 3-mode toggle
- **Styling:** shadcn/ui component library, Tailwind CSS v4
- **Font:** Geist Variable font
- **Animations:** CSS transitions and animations throughout

### Startup & Prewarm
- **Launch at login:** Windows registry autostart
- **Fast startup mode:** Cache prewarming (torch + model weights) via Windows Scheduled Task (with HKCU Run-key admin-free fallback)
- **Prewarm:** Background cache warming on logon (45s delay) and system idle (15 min)

### CLI
- `python -m voice_typer --version` — show version
- `python -m voice_typer --debug` — enable debug logging to console

### Native Settings Window
- Standalone tkinter settings window with collapsible Advanced section
- Hotkey, model, microphone, autostart, and notification settings
- SettingsController with callback-based side effects

## Quick Install

1. Go to **[Releases](https://github.com/AbdallahIsDev/voice-typer/releases)**
2. Download the latest `VoiceTyper-Setup-*.exe`
3. Double-click the installer — no Python, no terminal needed
4. Voice Typer starts automatically — look for the mic icon in the system tray

## Requirements

- Windows 10/11
- A microphone
- Internet on first run (downloads the Whisper model, ~466MB for the default small.en)

## For Developers (from source)

### Prerequisites

- **Python 3.10+** from [python.org](https://python.org)
- **Node.js 20+** from [nodejs.org](https://nodejs.org)

### Backend

```bash
pip install -e ".[test]"
pytest
```

### Frontend

```bash
cd voice_typer/client
npm install
```

Then build and launch from the project root:

```bash
npm run dev          # or yarn dev
```

Or use the dev helper:

```bash
python -m voice_typer.server.ipc_server --port 9876
# In another terminal:
cd voice_typer/client && npx electron-vite dev
```

### Optional ASR Backends

```bash
# Qwen3-ASR
pip install qwen-asr torch --index-url https://download.pytorch.org/whl/cpu

# NVIDIA Parakeet (auto-downloads weights on first load)
# No extra pip install needed — uses transformers from base deps
```

## Architecture

```
┌─────────────────────────────────────────────┐
│               Electron Shell                 │
│  Main Process (IPC bridge, window mgmt)     │
│  ↓ contextBridge ↑                           │
│  React Renderer (shadcn/ui, Tailwind v4)    │
│    Dashboard · Bubble overlay               │
└──────────────────┬──────────────────────────┘
                   │ TCP (port 9876)
┌──────────────────▼──────────────────────────┐
│              Python Backend                  │
│  VoiceTyperApp · Recorder · Transcriber     │
│  Text Cleanup · HistoryDB · Tray · Hotkeys  │
│  ASR Setup · Task Scheduler · Waveform      │
└─────────────────────────────────────────────┘
```

Key files:

```
voice_typer/
├── __init__.py           # Package init + metadata
├── __main__.py           # CLI entry point (--version, --debug)
├── server/
│   ├── app.py            # Orchestrator, state machine
│   ├── config.py         # Config dataclass, atomic JSON save
│   ├── recording.py      # Audio capture (sounddevice)
│   ├── transcription.py  # faster-whisper engine
│   ├── qwen_engine.py    # Qwen3-ASR backend
│   ├── parakeet_engine.py # NVIDIA Parakeet backend
│   ├── cloud_engines.py  # OpenAI/Groq/Deepgram ASR
│   ├── streaming.py      # Chunk-by-chunk streaming
│   ├── text_cleanup.py   # 9-step cleanup pipeline
│   ├── llm_polish.py     # OpenAI-compatible text polish
│   ├── vocabulary.py     # 6-category vocabulary manager
│   ├── templates.py      # Voice template expander
│   ├── history_db.py     # SQLite history
│   ├── clipboard.py      # Win32 auto-paste
│   ├── hotkeys.py        # Win32 native + pynput fallback
│   ├── tray.py           # pystray icon + menu
│   ├── hallucination.py  # Near-silence hallucination rejection
│   ├── audio_quality.py  # Clipping/low/noise detection
│   ├── crash_recovery.py # Unpasted text recovery
│   ├── onboarding.py     # 5-step wizard controller
│   ├── prewarm.py        # Cache prewarming (Task Scheduler)
│   ├── platform.py       # OS adapters
│   ├── autostart_launcher.py  # Build-first launch
│   ├── waveform.py       # Waveform bubble state + listeners
│   ├── settings.py       # Settings controller + native tkinter window
│   ├── asr_setup.py      # GPU detection, dep checks, pip install, weight download
│   ├── task_scheduler.py # Windows Scheduled Task registration (with HKCU Run-key fallback)
│   ├── ipc_server.py     # TCP IPC server for Electron communication
│   └── corrections.json  # Bundled speech corrections
├── client/
│   ├── src/main/         # Electron main process (window mgmt, bubble)
│   ├── src/preload/      # Context bridge
│   └── src/renderer/     # React app (8 pages, components, bubble)
│       ├── src/pages/    # Home, History, Templates, Vocabulary,
│       │                   Models, Microphone, Analytics, Settings
│       └── src/components/ # Shared UI components
└── tests/                # ~25 pytest files
```

## Dashboard Pages

| Page | Description |
|------|-------------|
| **Home** | Microphone control, real-time recording stats, dashboard overview |
| **History** | Paginated transcription log with full-text search, favorites, export (JSON/CSV), delete |
| **Templates** | Voice template management — create trigger phrases that expand with dynamic variables |
| **Vocabulary** | 6-category custom vocabulary (custom, medical, legal, technical, academic, personal) with import/export |
| **Models** | ASR model selection, cloud provider API keys (OpenAI, Groq, Deepgram), test connection |
| **Microphone** | Device list with host API info, live audio level meter, recommended device markers |
| **Analytics** | Daily transcription stats, 7-day activity chart, usage streaks |
| **Settings** | All configuration — 7 sections, 30+ settings |

## Settings

All settings are configurable from the Settings page in the dashboard (7 sections):

| Section | Settings |
|---------|----------|
| **General** | Launch at Login, Fast Startup (prewarm), Notifications, Theme (system/light/dark), Tray Click action |
| **Overlay** | Bubble behavior (show-on-record/always-visible/disabled), position (top/bottom), show on startup, drag to move |
| **Hotkey** | Dictation key (F2–F12) with composite modifier support |
| **Recording** | Toggle/Push-to-talk mode, auto-stop seconds, ESC to cancel, auto-paste, re-paste hotkey, silence warning, max duration |
| **Post-Processing** | Language selection, auto punctuation, text cleanup toggles, templates, vocabulary |
| **LLM Polishing** | Enable toggle, API key (show/hide), API URL, model name, preset (professional/casual/email/code) |
| **Audio & Recovery** | Crash recovery toggle, audio warnings (clipping/low volume/noisy input) |

## Project Status

Actively maintained. Standalone Windows installer available for each release.

## License

MIT