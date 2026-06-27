# Contributing to Voice Typer

Thank you for your interest in contributing! This document covers the basics.

## Development Setup

### Prerequisites

- **Python 3.10+** (3.12 recommended)
- **Node.js 20+** (for the Electron frontend)
- **Windows 10/11** (primary target; macOS/Linux have partial support)
- A microphone for testing dictation

### Getting Started

1. **Clone the repo:**
   ```bash
   git clone https://github.com/AbdallahIsDev/voice-typer.git
   cd voice-typer
   ```

2. **Set up Python environment:**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate

   pip install -e ".[test]"
   ```

3. **Set up Electron frontend:**
   ```bash
   cd voice_typer/client
   npm install
   ```

4. **Run the app in dev mode:**
   ```bash
   # Terminal 1: Python backend
   python -m voice_typer.server.ipc_server

   # Terminal 2: Electron frontend
   cd voice_typer/client
   npm run dev
   ```

## Running Tests

### Python Tests

```bash
pytest tests/ -v
```

Currently 1334+ tests pass. Tests cover IPC dispatch, config validation, ASR
engines, hotkey backends, recording, streaming, and end-to-end regression suites.

### Frontend Tests

```bash
cd voice_typer/client
npm test        # vitest
npm run lint    # eslint
npm run typecheck  # tsc --noEmit
```

## Code Style

### Python

- **Formatter:** ruff (line-length = 120)
- **Type checker:** pyrefly / mypy (Python 3.12 target)
- **Test framework:** pytest

Key conventions:
- Every fix should include a regression test.
- Use `log.exception()` for error paths, not bare `print()`.
- Never use `# type: ignore` or `except: pass` to suppress real issues.
- Document non-obvious decisions with `# NEW-XXX:` comments referencing the issue ID.

### Mock Import Convention

TEST-033: Always import mock objects from `unittest.mock` directly:

```python
# ✓ Correct
from unittest.mock import MagicMock, patch

# ✗ Wrong — do NOT use the `mock` module alias
from unittest import mock
mock.MagicMock(...)  # wrong
```

Rationale: `from unittest import mock` introduces an unnecessary indirection.
Importing the specific classes (`MagicMock`, `patch`, `call`, `PropertyMock`)
directly from `unittest.mock` is more explicit and avoids the `mock.XXX`
prefix pattern. The `monkeypatch` fixture from pytest is preferred for
attribute/item replacement since it auto-cleans up after each test.

### TypeScript

- **Formatter:** prettier
- **Linter:** eslint (max-warnings=0)
- **Type checker:** tsc --noEmit

Key conventions:
- Use the shared `useSnackbar` hook, not inline `useState` + `setTimeout`.
- Validate IPC responses at runtime before casting (see `asRecordingState`).
- Prefer the shared `usePython()` hook for all Python backend calls.

## Submitting Changes

1. **Fork** the repo and create a feature branch.
2. **Write tests** for your changes.
3. **Run the full test suite** — all tests must pass.
4. **Run lint + typecheck** — zero warnings.
5. **Open a Pull Request** with a clear description of what changed and why.

## Reporting Bugs

Use [GitHub Issues](https://github.com/AbdallahIsDev/voice-typer/issues) and
include:
- Voice Typer version (`python -m voice_typer --version`)
- OS and Python version
- Steps to reproduce
- Expected vs. actual behavior
- Log file (if applicable) — see the About/Diagnostics page in the app

## Architecture

See `FEATURES.md` and `docs/ARCHITECTURE.md` for the full architecture overview.
The key design decisions:

- **TCP IPC bridge** between Electron (frontend) and Python (backend)
- **CUDA-first** ASR with automatic CPU fallback
- **Multiple ASR backends:** faster-whisper, Qwen3, Parakeet, cloud (OpenAI/Groq/Deepgram)
- **Offline-first:** all processing happens locally after the first model download

## Questions?

Open an issue with the `question` label.
