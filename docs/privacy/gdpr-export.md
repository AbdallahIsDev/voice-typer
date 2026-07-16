# GDPR Right-to-Export — Feature Gap Outline (NEW-PRIV-007)

## Status

**Documented as a v1 feature gap.** Not implemented in this round.
This document outlines what a full GDPR Article 20 ("Right to data
portability") export for Voice Typer would need to include, so a
future implementer has a clear scope.

Voice Typer is a **local-first** desktop utility. The only existing
export, `service.export_diagnostics` (`service.py:1998`), produces a
**redacted support bundle** via `CrashRecovery.create_diagnostic_bundle()`
for troubleshooting — it is *not* a GDPR Art. 20 export of personal
data. Conflating the two would mislead users: a diagnostic bundle
redacts transcript text, whereas a GDPR export must include it.

## What a GDPR Art. 20 export should include

The export should be a single `.zip` (or tarball) containing
user-readable + machine-readable copies of every personal-data
artifact Voice Typer stores locally. All paths are relative to the
config dir (`_config_dir()`, typically `~/.config/voice-typer` on
Linux, `%APPDATA%/voice-typer` on Windows, `~/Library/Application
Support/voice-typer` on macOS).

| Artifact | Path | Format | Notes |
|---|---|---|---|
| Transcription history | `history.db` | SQLite | Already structured; also export as JSON for portability. |
| Crash-recovery buffer | `voice-typer-recovery.json` | JSON | Last 10 unpasted transcriptions (`crash_recovery.py`). |
| User config + consent flags | `config.json` | JSON | Includes `onboarding_completed`, `auto_punctuation`, `recording_mode`, hotkey prefs, theme, language. **Redact** `llm_api_key` / cloud-engine credentials. |
| User corrections | `voice-typer-corrections.json` | JSON | Custom misspelling/phrase corrections (`text_cleanup.py`). |
| Vocabulary / templates | `vocabulary.json`, `templates.json` | JSON | User-added entries. |
| Microphone-test recordings | `<config_dir>/mic-test-*.wav` | WAV | Only if the user ran the mic-test page and the files still exist. |
| Crash dumps | `<config_dir>/crash-*.dmp` | binary | OS-level crash dumps (Windows minidumps). |
| Logs | `voice-typer.log` | text | Already PIIRedactionFilter-redacted; include as-is. |
| Model artifacts | `<config_dir>/models/` | binary | Whisper / Parakeet / Qwen model weights. **Out of scope** for GDPR export (not personal data — publicly distributable weights). |
| Voice recordings (live dictation) | — | — | Voice Typer does **not** persist raw audio from live dictation — audio is processed in-memory and discarded after transcription. Mic-test recordings are the only persisted audio. |

## Suggested command surface

- **IPC command**: `export_gdpr_bundle` → returns `{"success": bool,
  "path": str}` (mirrors `export_diagnostics`).
- **Renderer**: Settings → Privacy → "Export my data (GDPR Article 20)".
- **Output**: a timestamped `.zip` at `_config_dir() /
  gdpr-export-YYYYMMDD-HHMMSS.zip` (and surface a save-dialog so the
  user can move it elsewhere).
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

## Out of scope for v1 (will not implement this round)

- Cloud-side export (Voice Typer has no server-side personal data;
  the only cloud calls are model downloads + optional LLM polish,
  both of which are stateless HTTP requests — no cloud account).
- Automated scheduled exports.
- Export of OS-level crash dumps (binary, OS-specific tooling
  needed).

## Related findings

- NEW-PRIV-008 — GDPR right-to-delete (see `gdpr-delete.md`).
- NEW-PRIV-003 — Restart subprocess env inheritance (separate issue;
  same `docs/privacy/` folder).
