# ADR 0003: Silero VAD Adoption

## Status

Accepted

## Date

2024-02-10

## Context

Voice Typer records audio continuously while the user presses the dictation hotkey.
Without Voice Activity Detection (VAD), the entire recording (including long silences)
is sent to the ASR model, wasting GPU/CPU time and producing hallucinated text during
silent segments.

Options considered:

1. **Energy-based VAD** (RMS threshold) — simple but unreliable; background noise triggers
   false positives, quiet speech triggers false negatives.
2. **WebRTC VAD** — fast, lightweight, but binary (speech/silence) with no confidence
   scores; requires compiled C extension.
3. **Silero VAD** — neural network-based, high accuracy, outputs confidence scores,
   available as a small ONNX model (~2MB), runs on CPU with minimal latency.
4. **No VAD** — send everything to Whisper and let it decide; wastes compute and produces
   hallucinations.

## Decision

We adopted **Silero VAD** (option 3) as the primary VAD method, with an energy-based
fallback when the ONNX runtime is unavailable. Silero provides per-frame speech
probabilities that we use to detect speech onset, trim silence from audio chunks, and
prevent hallucinated text.

## Consequences

### Positive
- High accuracy: Silero correctly identifies speech in noisy environments.
- Low latency: ONNX inference on CPU takes <5ms per 30ms frame.
- Small model: ~2MB, bundled with the app.
- Confidence scores: we can tune the threshold per-user or per-environment.

### Negative
- ONNX runtime dependency: adds ~15MB to the installer.
- GPU not needed: Silero runs on CPU, but the ONNX runtime may try to use GPU
  if available (we pin to CPU).
- Fallback complexity: the energy-based fallback must be maintained for systems
  where ONNX fails to load.
