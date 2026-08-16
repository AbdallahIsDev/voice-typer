# Voice Typer — Public API Reference (DEPRECATED)

> **NH-34 reconciliation (2026-07-24):** this file is **deprecated** and is
> retained only for backward-link compatibility. The canonical references are:
>
> - **[`docs/python-api.md`](python-api.md)** — Python class API reference
>   (`VoiceTyperApp`, `Recorder`, `TranscriptionEngine`, `Config`, etc.).
>   Kept in sync with the actual class signatures by
>   `tests/test_api_doc_accuracy.py`.
> - **[`docs/ipc-reference.md`](ipc-reference.md)** — IPC message reference
>   (the 69-command / 36-event surface — 67 renderer-reachable + 2 host-only
>   commands; 36 typed push events — grouped by namespace, with the
>   four-allowlist contract + per-command notes).
>
> New content MUST go in `docs/python-api.md` or `docs/ipc-reference.md`,
> not here. Inbound links should be updated to point at those two files
> directly; this file will be deleted in a future release once no inbound
> links remain.
>
> The Python class API (`VoiceTyperApp`, `Recorder`, `TranscriptionEngine`,
> `Config`, `ClipboardManager`, `ModelManager`, `SecurityModule`,
> `IpcServer`, etc.) is documented in `docs/python-api.md`. The IPC
> command surface (renderer → main → backend ↔ Rust host) is documented
> in `docs/ipc-reference.md`.

---

## Config

**Module:** `voice_typer.server.config`

> The Config reference below is duplicated from
> [`docs/python-api.md`](python-api.md#key-configuration-keys) because
> `tests/test_api_doc_accuracy.py` parses this table from `docs/API.md`.
> Do NOT edit the table here — edit it in `docs/python-api.md` and the
> table here will be reconciled in a follow-up that moves the test
> assertion to the canonical file.

### Key Configuration Keys

The defaults below are read from the `Config` dataclass in
`voice_typer/server/config.py` and the enum validators in
`voice_typer/server/config_validators.py`.  A CI test
(`tests/test_api_doc_accuracy.py`) parses this table and asserts each
row matches the actual `Config` default — if you change a default in
`Config`, update this table in the same commit or CI will fail.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `recording_mode` | `str` | `"toggle"` | One of: `toggle`, `push_to_talk`. |
| `model_size` | `str` | `"tiny"` | Model name (one of `ALLOWED_USER_MODELS` — the catalog: `tiny`, `large-v3`, `large-v3-turbo`, `parakeet`, `qwen`), or `""` for the genuine "no model selected" state (`NO_MODEL_SIZE` — the app loads nothing until the user picks a model). Default comes from `DEFAULT_MODEL_SIZE` in `model_registry.py`. |
| `language` | `str` | `"en"` | ISO-639-1 language code for transcription (e.g. `"en"`, `"fr"`, `"de"`). |
| `paste_on_stop` | `bool` | `True` | Whether to auto-paste transcribed text when recording stops. |
| `log_transcriptions` | `bool` | `False` | Whether to log transcription text (privacy-sensitive — see SEC-009). |
| `silence_warning_seconds` | `float` | `20.0` | Seconds of silence before the silence-warning tray notification fires. |
| `stop_on_silence_seconds` | `float` | `60.0` | Seconds of silence before auto-stop. |
| `clipboard_restore_delay_ms` | `int` | `150` | Delay (ms) between the paste keystroke and restoring the previous clipboard contents (ADR-0010). |
| `max_recording_time_seconds` | `int` | `900` | Hard cap on recording length (clamped to `[300, 3600]` — 5 to 60 minutes). |

Removed / renamed fields (documented for searchability — do NOT re-add):

- `paste_enabled` → renamed to `paste_on_stop`.
- `clipboard_clear_delay_seconds` → removed in ADR-0010 §8.2 (was dead
  code — only read by the deleted `schedule_clipboard_clear`).
- `check_updates` → never existed on `Config` (the auto-update flow is
  driven by Electron's `electron-updater`, not a Python config flag).
- `voice_activity` recording mode → never implemented; the enum is
  `{toggle, push_to_talk}` only.
- `model` → renamed to `model_size` (the IPC `set_config` allowlist key
  is `model_size`, not `model`).
