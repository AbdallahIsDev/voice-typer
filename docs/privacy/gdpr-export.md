# GDPR Right-to-Export — Feature Gap Outline (NEW-PRIV-007)

## Status

**Implemented** via `service.export_gdpr_bundle()` (CR-88 / Fix-D).
This document was originally a v1 feature-gap outline; it is now the
operational reference for what the GDPR Art. 20 ("Right to data
portability") export includes.

`service.export_gdpr_bundle()` produces a single timestamped `.zip`
at `<config_dir>/gdpr-export-YYYYMMDD-HHMMSS.zip` containing every
personal-data artifact the Python backend owns (the same set as
`delete_all_personal_data`). The export is the user's OWN data
verbatim — no redaction. Model weights are excluded (not personal
data). Atomic zip write (PI-14): the zip is built to a `.zip.tmp`
temp file and `os.replace`'d into place on success.

Voice Typer is a **local-first** desktop utility. The only existing
export, `service.export_diagnostics` (`service.py:1998`), produces a
**redacted support bundle** via `CrashRecovery.create_diagnostic_bundle()`
for troubleshooting — it is *not* a GDPR Art. 20 export of personal
data. Conflating the two would mislead users: a diagnostic bundle
redacts transcript text, whereas a GDPR export must include it.

## What the GDPR Art. 20 export includes

The export is a single `.zip` containing user-readable +
machine-readable copies of every personal-data artifact Voice Typer
stores locally. All paths are relative to the config dir
(`_config_dir()`, typically `~/.config/voice-typer` on Linux,
`%APPDATA%/voice-typer` on Windows, `~/Library/Application
Support/voice-typer` on macOS).

| Artifact | Path | Format | Notes |
|---|---|---|---|
| Transcription history | `history.db` | SQLite | Already structured; also export as JSON for portability. Included in export. |
| Crash-recovery buffer | `recovery.json` | JSON | Last 10 unpasted transcriptions (`crash_recovery.py`). Included in export. |
| User config + consent flags | `config.json` | JSON | Includes `onboarding_completed`, `auto_punctuation`, `recording_mode`, hotkey prefs, theme, language. **Redact** `llm_api_key` / cloud-engine credentials. Included in export. |
| User corrections | `voice-typer-corrections.json` | JSON | Custom misspelling/phrase corrections (`text_cleanup.py`). Included in export. |
| Vocabulary / templates | `vocabulary.json`, `templates.json` | JSON | User-added entries. Included in export. |
| Microphone-test recordings | `<config_dir>/mic-test-*.wav` | WAV | Only if the user ran the mic-test page and the files still exist. Included in export. |
| Logs (Python main) | `voice-typer.log` | text | Already PIIRedactionFilter-redacted; include as-is. Included in export. |
| Logs (Python main, rotated) | `voice-typer.log.1`..`voice-typer.log.5` | text | PI-4: rotated backups matched by `voice-typer.log.*` glob. Included in export. |
| Crash dumps (Windows VEH) | `<config_dir>/crash_diagnostics.*.txt` | text | PI-5: written by `crash_handler.py:722` as `crash_diagnostics.<PID>.txt`. The old `crash-*.dmp` glob was fictional. Included in export. |
| Crash dumps (Python excepthook) | `<config_dir>/python_crash.*.txt` | text | PI-5: written by `crash_handler.py:1190` as `python_crash.<PID>.txt`. Included in export. |
| Model artifacts | `<config_dir>/models/` | binary | Whisper / Parakeet / Qwen model weights. **Out of scope** for GDPR export (not personal data — publicly distributable weights). |
| Voice recordings (live dictation) | — | — | Voice Typer does **not** persist raw audio from live dictation — audio is processed in-memory and discarded after transcription. Mic-test recordings are the only persisted audio. |

## Suggested command surface

- **IPC command**: `export_gdpr_bundle` → returns `{"success": bool,
  "path": str}` (mirrors `export_diagnostics`).
- **Renderer**: Settings → Privacy → "Export my data (GDPR Article 20)".
- **Output**: a timestamped `.zip` at `_config_dir() /
  gdpr-export-YYYYMMDD-HHMMSS.zip` (and surface a save-dialog so the
  user can move it elsewhere). PI-14: written atomically via
  `.zip.tmp` + `os.replace` so a partial/corrupt zip is never left
  on disk.
- **Audit log**: record the export timestamp in `config.json` under a
  new `last_gdpr_export_at` field (not personal data in itself, but
  useful for the user to see when they last exercised the right).

## What `export_diagnostics` does NOT cover today

- It redacts transcript text from `history.db` (PII redaction by
  design — diagnostic bundles are for support tickets).
- It does not include `voice-typer-corrections.json` or
  `templates.json`.
- It does not include mic-test recordings.
- It does not produce a machine-readable sidecar (just the redacted
  SQLite + log).

## Out of scope

- Cloud-side export (Voice Typer has no server-side personal data;
  the only cloud calls are model downloads + optional LLM polish,
  both of which are stateless HTTP requests — no cloud account).
- Automated scheduled exports.
- Export of OS-level crash dumps (binary, OS-specific tooling
  needed).

## Related findings

- NEW-PRIV-008 — GDPR right-to-delete (see `gdpr-delete.md`).
- PI-4 / PI-5 / PI-14 — privacy/GDPR hardening (this round).
- NEW-PRIV-003 — Restart subprocess env inheritance (separate issue;
  same `docs/privacy/` folder).
