# AudioQualityController

**File**: `voice_typer/server/audio_quality_controller.py`

## Responsibility

The `AudioQualityController` handles the accumulation of audio quality metrics per chunk, manages on-the-fly reconstruction of filter chains when configurations change, and generates the final post-recording quality report. It was extracted from `VoiceTyperApp` during the RW-9 god-class decomposition.

Key responsibilities:
- Accumulate per-chunk audio quality metrics (RMS, peak, clipping, SNR)
- Rebuild the audio filter chain when configuration changes mid-recording
- Generate a post-recording quality report with warnings and recommendations
- Track recording-level statistics (average levels, duration, silence ratio)

## Entry Points

- **`accumulate_chunk(level_data)`** — called by the recording pipeline after each audio chunk is processed. Updates running quality metrics.
- **`reconfigure(changed_fields)`** — called when audio-related config fields change mid-recording. Rebuilds the filter chain for the new settings.
- **`finalize_report()`** — called at the end of a recording session. Returns a `QualityReport` dict with warnings, suggestions, and per-metric summaries.
- **`reset()`** — clears all accumulated state for a new recording session.

## IPC Surface

The `AudioQualityController` does not expose a direct IPC surface. Quality metrics are surfaced to the renderer via:
- **Push event** `"recording_quality"` — emitted periodically during recording with intermediate quality data.
- **Push event** `"recording_complete"` — emitted at the end of a recording, includes the final quality report.

Both events are published via the `event_bus` and forwarded to the Electron/Tauri renderer through the heartbeat or WS bridge.
