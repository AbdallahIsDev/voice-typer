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

- **`_on_audio_quality_chunk(rms: float, peak: float)`** — called by the recording pipeline after each audio chunk is processed. Updates running quality metrics (RMS, peak, clipping counter, SNR estimate, silence ratio) for the live `bubble_level` event and the post-recording report.
- **`_rebuild_audio_processor(force_sr: int | None = None)`** — called when audio-related config fields change mid-recording (or when the device's reported sample rate disagrees with the configured one). Tears down the existing filter chain and rebuilds it for the new settings without breaking the in-flight recording session. `force_sr` lets the caller pin the rebuild to a specific sample rate (used by the device-swap path).
- **`_finalize_audio_quality_report(audio: np.ndarray)`** — called at the end of a recording session with the final captured audio buffer. Computes the post-recording quality summary (peak, RMS, clipping samples, silence ratio, recommended gain), attaches it to the outgoing `recording_complete` event payload, and resets the per-session accumulators.
