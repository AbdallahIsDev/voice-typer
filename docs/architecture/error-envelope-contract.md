# Error Envelope Contract

Voice Typer uses two different error-envelope contracts depending on the IPC transport.

## TCP path (Electron host)

IPC handlers return `{"message": str(e)}` to the renderer. Detailed error messages are acceptable because the TCP path is loopback-only (`127.0.0.1:9876`) and benefits from diagnostic detail for development.

Example:
```json
{"id": 42, "ok": false, "message": "Model not loaded: whisper-tiny not found at /home/user/.cache/..."}
```

## WS path (Tauri host)

Unhandled exceptions return a generic envelope `{"code": "internal_error", "message": "internal error"}` to the renderer. Detailed error messages are hidden because the WS path is the production path and we don't want to leak internals to a potentially-compromised renderer.

Example:
```json
{"id": 42, "ok": false, "code": "internal_error", "message": "internal error"}
```

## Per-command validation errors

Both paths return the SAME per-command validation error envelopes (e.g. `{"code": "payload_too_large", "message": "..."}` from `_handle_save_templates`). These are explicit error codes the renderer can switch on.

## Rationale

The split is intentional: TCP is for development (Electron host), WS is for production (Tauri host). Documenting the contract here prevents future drift.
