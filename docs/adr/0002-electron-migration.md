# ADR 0002: Electron + Python Architecture (Initial)

## Status

Superseded by [ADR 0003](0003-electron-python-architecture.md) — the
"Refined" electron+Python architecture (ADR-0003) replaces this initial
design. This document is preserved for historical context.

## Date

2024-01-15

## Context

Voice Typer needs a rich, cross-platform user interface for settings, model management, and
recording feedback. The core ASR (Automatic Speech Recognition) pipeline relies on Python
libraries (faster-whisper, transformers, sounddevice) that have no equivalent in the
JavaScript/Node.js ecosystem. We needed an architecture that combines Python's ML/audio
strengths with a modern, accessible GUI.

The alternatives considered were:

1. **Pure Python GUI** (tkinter, PyQt, wxPython) — limited styling, poor accessibility,
   complex distribution.
2. **Python + WebView** (pywebview) — simpler but lacks process isolation; a crash in the
   renderer takes down the Python process.
3. **Electron + Python IPC bridge** — full separation of concerns, each process can crash
   independently, modern web UI with React.

## Decision

We chose **Electron + Python IPC bridge** (option 3). The Electron frontend provides a
modern, accessible UI using React + Tailwind CSS + shadcn/ui. The Python backend handles
all audio recording, model loading, and transcription. Communication happens over a local
TCP socket with a simple JSON protocol.

## Consequences

### Positive
- Clean separation: UI crashes don't lose in-progress transcriptions.
- Modern UI: React, Tailwind, shadcn/ui give us a professional look with minimal effort.
- Independent updates: the Electron app can be updated without touching the Python backend.
- Accessibility: web-based UI inherits the browser's accessibility tree.

### Negative
- Two processes to manage: the Electron app must discover and connect to the Python server.
- IPC overhead: JSON serialization over TCP adds ~1ms latency per message (acceptable for
  our use case).
- Larger distribution: Electron adds ~80MB to the installer size.
- Complexity: developers need both Node.js and Python toolchains.
