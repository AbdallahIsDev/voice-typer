"""Generate a 1-second 440Hz sine wave WAV file for testing.

Usage:
    python generate_fixture.py

Produces: tests/fixtures/test_440hz_1s_16k.wav
Format: 16-bit PCM, mono, 16000 Hz sample rate, 1 second duration.
"""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 16000
DURATION_S = 1.0
FREQUENCY = 440.0
AMPLITUDE = 0.5  # -6 dBFS


def generate():
    n_samples = int(SAMPLE_RATE * DURATION_S)
    samples = []
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        value = AMPLITUDE * math.sin(2 * math.pi * FREQUENCY * t)
        # Convert to 16-bit PCM
        pcm = int(value * 32767)
        pcm = max(-32768, min(32767, pcm))
        samples.append(pcm)

    # WR-1: resolve the output path relative to THIS file so the script
    # works regardless of the caller's CWD. The previous relative path
    # (`"tests/fixtures/test_440hz_1s_16k.wav"`) only worked when invoked
    # from the repo root; running `cd tests/fixtures && python
    # generate_fixture.py` would write to a nested non-existent path
    # (FileNotFoundError) or to the wrong location.
    output_path = Path(__file__).parent / "test_440hz_1s_16k.wav"
    with wave.open(str(output_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    print(f"Generated {output_path} ({n_samples} samples, {DURATION_S}s)")


if __name__ == "__main__":
    generate()
