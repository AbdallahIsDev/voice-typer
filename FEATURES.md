# Voice Typer — Features

Last updated: 2026-06-22

---

## Competitive Analysis (2026-06-02)

7 open-source projects analyzed. Key gaps identified in Voice Typer.

> **Note:** This competitive analysis is a snapshot from 2026-06-22.
> Star counts and feature sets may have changed since then.  To refresh,
> re-run the research against the latest GitHub data and update the
> table below.  The key takeaways (gaps in undo, a11y, cross-platform,
> privacy consent) are structural and unlikely to change without
> deliberate effort.

### Projects Analyzed

| Project | Stars | Language | Framework | Platform | ASR Engines |
|---|---|---|---|---|---|
| **Handy** | 23k | Rust + TypeScript | Tauri | Win/Mac/Linux | Whisper (whisper-rs), Parakeet V3 (transcribe-rs) |
| **Input0** | 268 | Rust + TypeScript | Tauri | macOS | Whisper, SenseVoice, Paraformer, Moonshine, FireRedASR, Zipformer CTC (6 engines, 12 models) |
| **Freestyle** | 231 | TypeScript + C | Electron | Win/Mac/Linux | OpenAI, Groq, Anthropic, Google, Deepgram, ElevenLabs (cloud APIs) + local Whisper, Parakeet |
| **Speed of Sound** | 147 | Kotlin | Java-GI/GTK | Linux | Sherpa ONNX — Whisper, Parakeet, Canary + more |
| **VOICE2TYPE** | 39 | Rust | Native Win32 | Windows | SiliconFlow/Groq cloud + local Whisper |
| **thinkur** | 23 | Swift | Native Xcode | macOS | Apple Speech Framework |
| **MoFA-IME** | 4 | Rust | Native Rust | macOS | Whisper + Qwen GGUF (llama.cpp) |
| **Voice Typer (ours)** | — | Python + TypeScript | Electron | Win/Mac/Linux | faster-whisper (ctranslate2), Qwen3-ASR, Parakeet TDT v3, cloud (OpenAI/Groq/Deepgram) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Electron Shell                        │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Main Process (main/index.ts)        │   │
│  │  · Single-instance lock                          │   │
│  │  · Spawns Python backend via pythonw             │   │
│  │  · TCP IPC bridge (port 9876)                    │   │
│  │  · 64 allowed IPC commands (allowlist)           │   │
│  │  · Periodic health check (60s)                   │   │
│  │  · Per-session auth token                        │   │
│  │  · Event nonce verification                      │   │
│  └──────────────┬───────────────────────────────────┘   │
│                 │                                       │
│  ┌──────────────▼───────────────────────────────────┐   │
│  │           Preload Bridge (preload/index.ts)      │   │
│  │  window.python · window.bubble · window.window_  │   │
│  └──────────────┬───────────────────────────────────┘   │
│                 │                                       │
│  ┌──────────────▼───────────────────────────────────┐   │
│  │              React Renderer                      │   │
│  │  · Dashboard window (frameless)                  │   │
│  │  · Bubble window (always-on-top)                 │   │
│  │  · 9 pages (Home, History, Templates, etc.)      │   │
│  │  · shadcn/ui + Tailwind v4 + Radix UI            │   │
│  │  · Dark/light/system theme via CSS vars          │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────────┘
                       │ TCP (port 9876)
┌──────────────────────▼──────────────────────────────────┐
│                  Python Backend                         │
│  voice_typer/server/                                    │
│  · VoiceTyperApp (orchestrator)                         │
│  · VoiceTyperService (service layer, ARCH-005)          │
│  · IPC server (TCP 9876, rate limiter, SEC-018/019)     │
│  · Config (JSON, atomic save, schema versioning)        │
│  · Recorder (sounddevice, silence/VAD detection)        │
│  · ModelManager (ASR lifecycle, ARCH-006)               │
│  · RecordingController (recording lifecycle, #2 R9)     │
│  · DictationPipeline (testable steps)                   │
│  · Transcription (faster-whisper, ctranslate2)          │
│  · Qwen engine · Parakeet engine · Cloud engines        │
│  · Streaming transcription (chunk-by-chunk)             │
│  · AudioProcessor (VAD-based noise gate)                │
│  · Silero VAD (waveform noise gating)                   │
│  · Text cleanup (9-step pipeline)                       │
│  · LLM polisher (OpenAI-compatible, 4 presets)          │
│  · HistoryDB (SQLite, WAL mode, favorites)              │
│  · Vocabulary manager (6 categories)                    │
│  · Template manager (trigger-expand + variables)        │
│  · Volume ducking (smart duck, fade ramp)               │
│  · Hotkey backend (Win32 native + pynput fallback)      │
│  · Hotkey dispatcher                                    │
│  · Tray icon (pystray, decomposed ARCH-003)             │
│  · Crash recovery buffer · Duck crash recovery          │
│  · Audio quality analyzer                               │
│  · Hallucination rejection                              │
│  · Onboarding controller (6-step wizard)                │
│  · Help overlay (`?`) + punctuation cheat sheet         │
│  · Prewarm + Task Scheduler                             │
│  · Autostart launcher (build-first)                     │
│  · Platform adapters (autostart, mic listing, ducking)  │
└─────────────────────────────────────────────────────────┘
```

---

## All Features

### Recording & Audio

| # | Feature | Status | Notes |
|---|---|---|---|
| 1 | Global hotkey dictation (Caps Lock default on all platforms; remappable) | ✅ | Win32 `RegisterHotKey` + `GetAsyncKeyState` polling; pynput fallback |
| 2 | Toggle mode (press-on/press-off) | ✅ | Configurable in Settings |
| 3 | Push-to-talk mode (hold to record) | ✅ | Release callback wired in both hotkey backends |
| 4 | Streaming transcription (chunk-by-chunk during recording) | ✅ | `StreamingTranscriptionSession` with overlapping windows, guard regions, word timings |
| 5 | Silence detection + auto-stop | ✅ | Configurable silence seconds before stop (1/2/3/5 min) |
| 6 | Silence warning with exponential backoff | ✅ | Configurable seconds before warning |
| 7 | Max duration auto-stop | ✅ | GPU default longer than CPU; 0 = auto |
| 8 | Mic disconnect detection | ✅ | Variance-based H12 detection |
| 9 | ESC to cancel dictation | ✅ | Global hotkey listener for Escape |
| 10 | Crash recovery (unpasted transcriptions saved to disk) | ✅ | Crash-safe file buffer, restored on next launch |
| 11 | Hallucination rejection | ✅ | Shared between Whisper and Qwen engines, detects near-silence hallucinations |
| 12 | Volume ducking (auto duck/release system volume) | ✅ | VolumeDucker with fade ramp, mute-state preservation, manual-override detection |
| 13 | Smart duck (skip ducking if no audio playing) | ✅ | v2.2: checks speaker activity before ducking |
| 14 | Silero VAD noise gating for waveform visualizer | ✅ | Lazy-loaded ~2MB model, gates waveform updates on voice activity |
| 15 | AudioProcessor with VAD-based noise gate | ✅ | Filters ambient noise from recorded audio |

### ASR Backends

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 16 | faster-whisper (tiny.en, small.en, medium.en) | ✅ | ctranslate2 backend, GPU→CPU 4-level automatic fallback |
| 17 | Qwen3-ASR-0.6B | ✅ | Local model via `qwen-asr` package |
| 18 | NVIDIA Parakeet TDT v3 | ✅ | HuggingFace Transformers, auto-download weights |
| 19 | Cloud ASR — OpenAI Whisper API | ✅ | `CloudEngine` interface, tested |
| 20 | Cloud ASR — Groq Whisper API | ✅ | `CloudEngine` interface, tested |
| 21 | Cloud ASR — Deepgram API (nova-2) | ✅ | `CloudEngine` interface, tested |
| 22 | GPU→CPU automatic fallback chain | ✅ | 4 levels: CUDA→CUDA (fallback)→CPU (float16)→CPU (float32) |
| 23 | ASR backend registry (AsrBackendRegistry) | ✅ | Single source of truth for all engine instances |
| 24 | Model download management | ⚠️ | Download progress bar renders; real download is simulated |
| 25 | Model benchmark | ⚠️ | Button exists; benchmark is simulated |

### Post-Processing Pipeline

Pipeline order: Transcribe → Text Cleanup → Vocabulary → Templates → LLM Polish → Auto-Punctuate → Paste

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 26 | Text cleanup (9-step pipeline) | ✅ | Fix misspellings, remove repeats, capitalize sentences, question detection, etc. |
| 27 | Auto-punctuation | ✅ | Adds periods, commas, question marks |
| 28 | Custom vocabulary (6 categories) | ✅ | Merged bundled + user vocabularies; add/edit/delete in UI |
| 29 | Voice templates (trigger→expand) | ✅ | Trigger phrase + output text; 4 variables: {today}, {now}, {clipboard}, {username} |
| 30 | LLM text polishing | ✅ | OpenAI-compatible API; 4 presets: professional, casual, email, code |
| 31 | Language selection (25 languages + auto-detect) | ✅ | Configurable in Settings |
| 32 | Auto-paste into focused field | ✅ | pyperclip + Win32 SendInput (atomic Ctrl+V) |
| 33 | Repaste last transcription | ✅ | Global hotkey (default Ctrl+Alt+V) or tray menu item |

### History & Data

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 34 | Transcription history (SQLite) | ✅ | WAL mode, schema versioning, migrations |
| 35 | History search | ✅ | Free-text search, filtered by date range, limit bounding |
| 36 | History favorites | ✅ | Star toggle, filter by favorites |
| 37 | History export (JSON + CSV) | ✅ | Save dialog via Electron IPC |
| 38 | History clear all (with confirmation) | ✅ | Confirmation dialog |
| 39 | Vocabulary export (JSON + CSV) | ✅ | Save dialog via Electron IPC |

### Window Management

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 40 | Dashboard window | ✅ | Custom title bar with drag region |
| 41 | Bubble window (always-on-top) | ✅ | Frameless, skip-taskbar, focusable:false, visibleOnFullScreen |
| 42 | Close-to-tray (X button hides, doesn't quit) | ✅ | `preventDefault` on close event |
| 43 | Hidden startup mode (`VT_START_HIDDEN=1`) | ✅ | Creates window hidden + skipTaskbar; second-instance shows it |
| 44 | Single-instance guard | ✅ | Electron `requestSingleInstanceLock`; Python named mutex (dual enforcement) |
| 45 | Custom title bar with min/max/close | ✅ | TitleBar component with maximize state tracking |
| 46 | Collapsible sidebar | ✅ | w-12 / w-55 toggle, Ctrl+B shortcut |
| 47 | Bubble drag-to-move | ✅ | IPC-based delta positioning |
| 48 | Bubble position setting (top / bottom center) | ✅ | Settings + IPC handler |

### Theme & Appearance

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 49 | Dark / light / system theme | ✅ | 3-mode toggle; CSS custom properties with `.dark` class |
| 50 | shadcn/ui styling | ✅ | radix-luma style, zinc base color |
| 51 | Geist Variable font | ✅ | Geist Sans |
| 52 | Hugeicons Core Free icons | ✅ | SVG icon library |
| 53 | CSS animations (fade, scale, slide, pulse, glow) | ✅ | Custom keyframes in index.css |
| 54 | Custom scrollbar styling | ✅ | Webkit scrollbar with border-radius |
| 55 | Frameless window border treatment | ✅ | `rounded-lg border` when not maximized, removed when maximized |
| 56 | Recording waveform bubble animation | ✅ | 28 animated SVG bars, mic icon, speaking/listening label, enter/exit animations |

### Tray & Notifications

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 57 | System tray icon | ✅ | pystray with dynamic state colors (idle/recording/transcribing), DPI-aware sizing |
| 58 | Tray right-click menu | ✅ | Toggle Dictation → Open App → Models → Restart → Quit (cached, lazy rebuild) |
| 59 | Desktop notifications | ✅ | Tray `notify()` with configurable toggle; friendly error messages |
| 60 | Left-click tray action configurable | ✅ | open_app or toggle_dictation |

### Startup & Autostart

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 61 | Launch at login | ✅ | Windows Registry `HKCU\Run` |
| 62 | Fast startup (cache prewarming) | ✅ | `prewarm.py` + `task_scheduler.py`: preloads torch/transformers into OS standby cache |
| 63 | Desktop shortcut creation on first run | ✅ | Creates Voice Typer.lnk |
| 64 | Build-first launch strategy | ✅ | `autostart_launcher.py` builds then launches Electron; port-availability check for idempotency |

### UI Pages

| # | Page | Status | Contents |
|---|---|---|---|---|
| 65 | Home | ✅ | Mic/stop button, status indicator, hotkey display, last text preview, today's stats (3 cards), recent activity list |
| 66 | History | ✅ | Paginated list (50/page), search, favorites filter, delete, clear all, export JSON/CSV |
| 67 | Templates | ✅ | Trigger→output pairs; add/edit dialog; exact/contains match; 4 variables |
| 68 | Vocabulary | ✅ | add/edit/search; export JSON/CSV |
| 69 | Models | ✅ | Model selection (5 options); download progress; API keys (OpenAI/Groq/Deepgram); test connection; benchmark |
| 70 | Microphone | ✅ | Device list; system default; select/activate; live level meter; test start/stop; channels/rate |
| 71 | Analytics | ✅ | Today's stats (4 cards); 7-day bar chart; quick stats (model/device/language); streaks; all-time totals |
| 72 | Settings | ✅ | 8 sections: General, Overlay, Hotkey, Recording, Post-Processing, LLM Polishing, Audio & Recovery, Troubleshooting |
| 73 | Onboarding | ✅ | 6-step wizard (Welcome, Microphone, Permissions, Hotkey, Model, Done); created on first run detection. The Permissions step gates advancement on macOS (Accessibility) and Linux (input group); on Windows it auto-passes. |

### Settings (configurable via UI)

| Setting | Section | Type |
|---|---|---|
| Launch at Login | General | Toggle |
| Fast Startup (prewarm) | General | Toggle |
| Notifications | General | Toggle |
| Theme (system/light/dark) | General | 3-way |
| Left-click tray action | General | Select (open_app / toggle_dictation) |
| Bubble behavior (show on record / always visible) | Overlay | Select |
| Bubble position (top / bottom) | Overlay | Select |
| Show bubble on app startup | Overlay | Toggle |
| Drag to move bubble | Overlay | Toggle |
| Volume ducking enabled | Audio & Recovery | Toggle |
| Duck level (%) | Audio & Recovery | Number |
| Smart duck enabled | Audio & Recovery | Toggle |
| Dictation hotkey (Caps Lock default; customizable) | Hotkey | Select |
| Recording mode (toggle / push-to-talk) | Recording | Select |
| Auto-stop silence seconds (1/2/3/5) | Recording | Select |
| ESC to cancel | Recording | Toggle |
| Auto-paste | Recording | Toggle |
| Re-paste hotkey | Recording | Text (default Ctrl+Alt+V) |
| Silence warning seconds | Recording | Number |
| Max recording duration | Recording | Number (0 = auto) |
| Language (25 + auto-detect) | Post-Processing | Select |
| Auto punctuation | Post-Processing | Toggle |
| Text cleanup | Post-Processing | Toggle |
| Text snippets (templates) | Post-Processing | Toggle |
| Vocabulary corrections | Post-Processing | Toggle |
| LLM polishing enabled | LLM Polishing | Toggle |
| LLM API key | LLM Polishing | Password (show/hide) |
| LLM API URL | LLM Polishing | Text |
| LLM model | LLM Polishing | Text |
| LLM preset (professional/casual/email/code) | LLM Polishing | Select |
| Crash recovery | Audio & Recovery | Toggle |
| Audio warnings | Audio & Recovery | Toggle |
| View logs | Troubleshooting | Button |
| Reset to defaults | Troubleshooting | Button |

### Developer / Build

| # | Feature | Status | Notes |
|---|---|---|---|---|
| 74 | electron-vite build system | ✅ | Vite 7 for main/preload/renderer |
| 75 | electron-builder packaging | ✅ | NSIS installer (electron-builder.yml) |
| 76 | Python backend bundled as pip package | ✅ | setuptools, installed via pip |
| 77 | CI build pipeline (GitHub Actions) | ✅ | `.github/workflows/build.yml` |
| 78 | Diagnostics scripts | ✅ | F2 hotkey test, CUDA fallback, runtime proof |
| 79 | Test suite (700+ pytest files, 250+ vitest files; 2800+ Python tests) | ✅ | All major subsystems covered (counts grow over time — see `pytest --collect-only` and `npm run test` for the current totals) |
| 80 | Ruff linting + mypy type checking | ✅ | Configured in pyproject.toml |
| 81 | IPC command allowlist | ✅ | 67 allowed commands whitelisted in the Electron main process `ALLOWED_COMMANDS` set (`voice_typer/client/src/main/allowed-commands.ts`); the Python `_COMMAND_REGISTRY` registers 69 commands total. Two of those are intentionally absent from the renderer allowlist — `tray_click` (Rust-only, routed via `dispatch_inner` from the tray handler) and `shutdown` (cooperative shutdown is sent via `shutdown_sidecar` directly, not via the generic dispatch path) — so the renderer-callable count is 67 (== the renderer allowlist count). The +2 host-only delta is asserted by `_HOST_ONLY_COMMANDS` in `tests/test_security_doc_command_count.py`. CR-18 reconciliation 2026-07-19; re-verified 2026-07-24 (S4-CR-18 follow-up: 78/59 stale counts across CHANGELOG/FEATURES/SECURITY/CONTRIBUTING reconciled to 64/62/66; +2 again 2026-08-10: `reset_macos_accessibility` + `reset_linux_permissions`, now 66/64/68; +1 2026-08-10: `check_accessibility` re-added for the Settings → Troubleshooting stale-grant reset (finding #919 part b), now 67/65/69). Count is enforced by `tests/test_security_doc_command_count.py` + `tests/test_rust_allowlist_parity.py` + `tests/test_electron_ipc_and_build.py`. |
| 82 | IPC rate limiter | ✅ | Sliding window: 60 msg/s sustained, 200 burst |
| 83 | IPC auth token | ✅ | Per-launch random 256-bit token exchanged on TCP connect |
| 84 | Config secret redaction | ✅ | API keys replaced with `<redacted>` sentinel in IPC responses |

---

### Help Overlay & Punctuation Cheat Sheet

| # | Feature | Status | Notes |
|---|---|---|---|
| 85 | Help overlay (`?` shortcut) | ✅ | Press `?` anywhere in the app to open a modal listing every keyboard shortcut (dictation hotkey, `Esc`, `Ctrl+Alt+V` repaste, `Ctrl+B` sidebar, `Ctrl+,` settings, `Ctrl+H` home, `Tab`/`Shift+Tab` navigate, `Space` toggle, `Enter` activate, `Ctrl+Plus`/`Ctrl+Minus` zoom, `?` open help, `Alt+Left`/`Alt+Right` navigate back/forward). Rendered by `App.tsx:showHelpOverlay` state in a `Modal`. |
| 86 | Punctuation cheat sheet | ✅ | Embedded in the help overlay (`PunctuationCheatSheet.tsx`). Lists the spoken-form → character mappings Voice Typer recognizes: comma, period, question mark, exclamation point, semicolon, colon, apostrophe, open/close quote, new line (↵), new paragraph (¶). Source of truth: `voice_typer/client/src/renderer/src/components/help/PunctuationCheatSheet.tsx`. |

---

## Features Status — gaps that remain

| Feature | Found In | Our Status |
|---|---|---|
| Multi-language Whisper models (non-English) | Handy, Input0 | ❌ Only `.en` variants offered |
| Always-listening (VAD-driven auto-start) | Handy, Freestyle | ❌ No Silero-VAD or webrtcvad |
| Self-hosted LLM as transcription backend (Ollama, vLLM, llama.cpp) | Speed of Sound, MoFA-IME | ❌ LLM only for polish, not ASR |
| ONNX runtime as inference backend | Speed of Sound (Sherpa ONNX) | ❌ None |
| Meeting recording with speaker labels / diarization | thinkur | ❌ None |
| MCP integration (AI tools read transcripts) | thinkur | ❌ None |
| Per-app writing styles / context-aware profiles | thinkur, Freestyle | ❌ None |
| CLI flags for remote control | Handy | ❌ None |
| Onboarding wizard UI | — | ✅ Full 6-step React wizard implemented (Welcome → Microphone → Permissions → Hotkey → Model → Done) |
| Model download (real implementation) | — | ⚠️ Progress bar renders, download is simulated |
| Model benchmark (real implementation) | — | ⚠️ Button exists, benchmark is simulated |
| Microphone test (real audio capture) | — | ✅ Real capture via `level_monitor.start_test_recording()` which opens a live `sounddevice.InputStream` and returns recorded audio |

### Distribution — gaps that remain

| Method | Projects Using | Our Status |
|---|---|---|
| Homebrew cask | Handy, Input0 | ❌ None |
| winget | Handy | ❌ None |
| Flathub | Speed of Sound | ❌ None |
| Snap Store | Speed of Sound | ❌ None |
| Code signing (no SmartScreen warning) | — | ❌ None |
| Auto-update mechanism | — | ❌ None |

---

## What Voice Typer Does Well (competitors don't)

| Feature | Notes |
|---|---|
| Streaming transcription (chunk-by-chunk during recording) | None of the competitors mention this — they all wait until recording stops |
| Advanced text cleanup pipeline (9 steps) | Most competitors have basic cleanup or rely on LLM polish |
| Hallucination rejection | Detects Whisper hallucinations on near-silence audio (`hallucination.py` shared by both engines) |
| Mic disconnect detection | Variance-based detection of dead mic |
| Exponential-backoff silence warnings | Unique safety feature |
| GPU→CPU automatic fallback chain | Robust 4-level fallback |
| Multiple ASR backends (Whisper + Qwen + Parakeet + cloud) | Most competitors specialize in one or two engines |
| Memory-efficient (200MB steady) | Most Tauri/Rust apps are similar, but Python at 200MB is good |
| Volume ducking with smart duck (auto-detect playback) | None of the competitors auto-duck system audio during dictation |
| IPC security (auth token, allowlist, rate limiting, secrets redaction) | None of the competitors implement defense-in-depth for local IPC |

---

## Feature Interaction Matrix (partial + not-implemented only)

| Feature | Interferes With | Helps With | Status |
|---|---|---|---|---|
| Multi-language models | None | Non-English users | ❌ |
| Always-listening | None | Hands-free | ❌ |
| Onboarding wizard | None | First-run UX | ✅ Implemented |
| Model download (real) | None | Usability | ⚠️ Simulated |
| Model benchmark (real) | None | Confidence | ⚠️ Simulated |
| Microphone test (real) | None | Confidence | ✅ Real audio capture via `level_monitor.start_test_recording()` |

No features conflict. All can coexist once completed.
