# Changelog

## 1.0.0 (current)

- Dual ASR backends: Whisper (faster-whisper, default) and optional Qwen3-ASR-0.6B
- Hidden streaming transcription with overlapping audio windows, timestamp-based dedup, and batch fallback
- Text cleanup pipeline: duplicate removal, whisper hallucination cleanup, misspelling correction, phrase substitution, self-correction cleanup, sentence capitalization, pronoun-I capitalization
- Tkinter-based settings window with collapsible Advanced section
- System tray icon (pystray) with dynamic menu: hotkey selection, microphone switching, model selection, autostart toggle, notifications toggle
- Win32 RegisterHotKey with GetAsyncKeyState polling (primary) + pynput fallback
- Composite hotkey support: `<ctrl>+<alt>+f2`, `<ctrl>+1` through `<ctrl>+5`, and F1-F12
- Safe auto-paste with terminal emulator detection (Shift+Insert), focus retry, and unknown-focus opt-in
- Microphone fallback chain: same-name candidates across host APIs, ranked by reliability, falling back to all available devices
- O(1) deque buffer with 30k chunk hard cap and telemetry at 1k/5k chunks
- Win32 console control handler for tray app survival after console close
- 4-level GPU->CPU model load fallback (configured device -> CPU/int8 -> CPU/int8 tiny.en -> CPU/float32 tiny.en)
- HTTP/2 support for faster-whisper model downloads via httpx
- NVIDIA wheel DLL path discovery for CUDA on Windows
- Pyrefly type checking across the entire codebase
- External corrections JSON override file
- Rotating log file handler with structured logging (session ID)
- 14 test files (~4000 lines), 36 commits
