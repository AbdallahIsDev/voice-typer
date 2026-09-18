# ADR 0003: Electron + Python Architecture

## Status

**Status: SUPERSEDED — Electron host removed 2026-09-17; Tauri is sole host (ADR-0020). Historical record only.**

Superseded by ADR-0020 (Tauri + Python sidecar). Preserved for historical
context (the original two-process Electron + Python design).

## Context

Voice Typer needs a desktop GUI for settings, onboarding, and visual feedback.
The transcription engine is Python-based (using PyTorch/CTranslate2).

## Decision

We use Electron for the UI and Python for the backend, communicating via
a local TCP IPC protocol. This allows us to leverage the Python ML ecosystem
while providing a modern, cross-platform UI.

## Consequences

- Two processes must be managed (Electron + Python)
- IPC adds latency for settings changes
- Build/packaging complexity increases (PyInstaller + Electron)
- Rich UI possible with React components
