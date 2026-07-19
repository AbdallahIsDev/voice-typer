# Module Documentation

This directory contains short dedicated doc pages for modules introduced during the RW-9 god-class decomposition and the ADR-0020 desktop runtime migration. Each page covers the module's responsibility, entry points, and IPC surface.

## Modules

| Module | Responsibility |
|--------|---------------|
| [`shutdown_controller`](shutdown_controller) | Manages the entire cleanup and shutdown lifecycle |
| [`audio_quality_controller`](audio_quality_controller) | Accumulates audio quality per chunk and generates post-recording quality reports |
| [`sidecar_ws`](sidecar_ws) | WebSocket server side of the Tauri↔Python bridge |
| [`prewarm_resolver`](prewarm_resolver) | Resolves the frozen prewarm executable path across platforms |
