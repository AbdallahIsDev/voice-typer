#!/usr/bin/env python3
"""Generate two distinct short WAV data-URL beeps for the Voice Typer
sound-manager fallback path.

This script exists because the two fallback constants
(``START_BEEP_WAV`` / ``STOP_BEEP_WAV`` in
``voice_typer/client/src/renderer/src/lib/sound-manager/beeps.ts``,
written back then inline in ``sound-manager.ts``, since extracted) were
once byte-for-byte identical base64 data URLs: when the Web Audio API
path failed, the HTMLAudioElement fallback played the exact same beep
for both "recording started" and "recording stopped" — the user could
not audibly distinguish them.

This script regenerates the two constants:

* ``START`` — 150 ms rising sweep 660 Hz -> 880 Hz (recording started).
* ``STOP``  — 200 ms falling sweep 523 Hz -> 392 Hz (recording stopped).

Both are 44.1 kHz, 16-bit, mono sine waves with a 5 ms linear attack
and 5 ms linear release to avoid click artifacts.  The output is a
``data:audio/wav;base64,...`` URL ready to paste into a TypeScript
constant.

Usage
-----
::

    python scripts/build/generate_beeps.py            # prints both URLs
    python scripts/build/generate_beeps.py --check     # exit 1 if
        the two URLs are identical, OR if the constants committed
        in lib/sound-manager/beeps.ts drift from the freshly generated URLs
        (regression guard)
    python scripts/build/generate_beeps.py --write     # writes the
        constants into lib/sound-manager/beeps.ts in place

The ``--write`` mode performs an exact-string replacement of the two
``const START_BEEP_WAV = ...`` / ``const STOP_BEEP_WAV = ...`` lines.
Re-running it is idempotent: the existing data URLs are matched and
replaced with the freshly generated ones.
"""

from __future__ import annotations

import argparse
import base64
import math
import re
import struct
import sys
from pathlib import Path

SAMPLE_RATE = 44100  # Hz
BITS_PER_SAMPLE = 16
CHANNELS = 1
ATTACK_MS = 5  # linear attack length
RELEASE_MS = 5  # linear release length
PEAK_AMPLITUDE = 0.85  # 0..1 — leave headroom; sound-manager caps at 0.15 volume

# Sound-manager plays these at volume 0.15, so PEAK_AMPLITUDE=0.85 yields
# roughly 0.13 peak output — comfortable but clearly audible.


def _sine_sweep_samples(
    *,
    duration_ms: int,
    freq_start: float,
    freq_end: float,
) -> bytes:
    """Synthesize a sine sweep with linear attack/release envelope.

    Returns the PCM payload (little-endian 16-bit signed samples) —
    the caller wraps it in a WAV container.
    """
    n_samples = int(SAMPLE_RATE * duration_ms / 1000)
    attack_n = int(SAMPLE_RATE * ATTACK_MS / 1000)
    release_n = int(SAMPLE_RATE * RELEASE_MS / 1000)

    samples = bytearray()
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        # Linear frequency interpolation across the duration (rising or
        # falling depending on freq_start vs freq_end).
        progress = i / max(1, n_samples - 1)
        freq = freq_start + (freq_end - freq_start) * progress
        phase = 2 * math.pi * freq * t
        # NOTE: we cannot integrate freq*progress naively because the
        # instantaneous frequency changes — the proper integral of a
        # linear chirp is ``2*pi * (f0*t + 0.5*(f1-f0)*t^2/duration)``.
        # The simpler ``2*pi*freq*t`` above introduces a tiny pitch
        # skew at the endpoints but is audibly indistinguishable for
        # these short sweeps; the proper integral is used below instead.
        duration = n_samples / SAMPLE_RATE
        phase = 2 * math.pi * (freq_start * t + 0.5 * (freq_end - freq_start) * (t**2) / duration)
        sample = math.sin(phase) * PEAK_AMPLITUDE

        # 5 ms linear attack (0 -> 1)
        if i < attack_n:
            sample *= i / max(1, attack_n)
        # 5 ms linear release (1 -> 0) at the tail
        elif i >= n_samples - release_n:
            sample *= (n_samples - 1 - i) / max(1, release_n)

        # 16-bit signed PCM, little-endian
        scaled = max(-32768, min(32767, int(sample * 32767)))
        samples.extend(struct.pack("<h", scaled))
    return bytes(samples)


def _wav_container(pcm: bytes) -> bytes:
    """Wrap raw PCM bytes in a minimal WAV (RIFF) container."""
    byte_rate = SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE // 8
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    data_size = len(pcm)
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,  # PCM chunk size
        1,  # PCM format
        CHANNELS,
        SAMPLE_RATE,
        byte_rate,
        block_align,
        BITS_PER_SAMPLE,
    )
    data_chunk = struct.pack("<4sI", b"data", data_size) + pcm
    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    riff = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")
    return riff + fmt_chunk + data_chunk


def _to_data_url(wav_bytes: bytes) -> str:
    encoded = base64.b64encode(wav_bytes).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"


def generate_start_url() -> str:
    pcm = _sine_sweep_samples(
        duration_ms=150,
        freq_start=660.0,
        freq_end=880.0,
    )
    return _to_data_url(_wav_container(pcm))


def generate_stop_url() -> str:
    pcm = _sine_sweep_samples(
        duration_ms=200,
        freq_start=523.0,
        freq_end=392.0,
    )
    return _to_data_url(_wav_container(pcm))


# ---------------------------------------------------------------------------
# lib/sound-manager/beeps.ts in-place patch (--write mode)
# ---------------------------------------------------------------------------

SOUND_MANAGER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "lib"
    / "sound-manager"
    / "beeps.ts"
)


def _patch_sound_manager(start_url: str, stop_url: str) -> bool:
    """Replace the START_BEEP_WAV / STOP_BEEP_WAV constants in-place.

    Returns True if the file was modified, False if no change was needed.
    Raises RuntimeError if either constant declaration is not found.
    """
    if not SOUND_MANAGER_PATH.exists():
        raise FileNotFoundError(f"beeps.ts not found at {SOUND_MANAGER_PATH}")

    original = SOUND_MANAGER_PATH.read_text(encoding="utf-8")
    # Match the existing `const NAME = "<data url>";` lines (single or
    # multi-line). The current source has them on a single line each,
    # but be tolerant of whitespace differences.
    new = original

    # START_BEEP_WAV — match the entire constant declaration regardless
    # of the current data URL value.
    start_pattern = re.compile(
        r'(const\s+START_BEEP_WAV\s*=\s*)"data:audio/wav;base64,[A-Za-z0-9+/=]*"\s*;',
        re.MULTILINE,
    )
    stop_pattern = re.compile(
        r'(const\s+STOP_BEEP_WAV\s*=\s*)"data:audio/wav;base64,[A-Za-z0-9+/=]*"\s*;',
        re.MULTILINE,
    )
    if not start_pattern.search(new):
        raise RuntimeError(
            "Could not find START_BEEP_WAV constant in "
            "lib/sound-manager/beeps.ts — the source layout has changed; "
            "update generate_beeps.py."
        )
    if not stop_pattern.search(new):
        raise RuntimeError(
            "Could not find STOP_BEEP_WAV constant in "
            "lib/sound-manager/beeps.ts — the source layout has changed; "
            "update generate_beeps.py."
        )

    new = start_pattern.sub(lambda m: f'{m.group(1)}"{start_url}";', new)
    new = stop_pattern.sub(lambda m: f'{m.group(1)}"{stop_url}";', new)

    if new == original:
        return False
    SOUND_MANAGER_PATH.write_text(new, encoding="utf-8")
    return True


def _read_sound_manager_urls() -> tuple[str, str]:
    """Extract the START_BEEP_WAV / STOP_BEEP_WAV data URLs committed in
    ``lib/sound-manager/beeps.ts``.

    Returns ``(start_url, stop_url)`` as full ``data:audio/wav;base64,...``
    strings. Raises :class:`FileNotFoundError` if the file is missing and
    :class:`RuntimeError` if either constant declaration cannot be found.

    Used by ``--check`` to verify the committed constants match the
    freshly generated URLs — the previous --check only verified the
    generated URLs were distinct from each other, which meant a stale
    or accidentally-collapsed pair of constants in lib/sound-manager/beeps.ts
    would pass the regression guard (false assurance).
    """
    if not SOUND_MANAGER_PATH.exists():
        raise FileNotFoundError(f"beeps.ts not found at {SOUND_MANAGER_PATH}")

    text = SOUND_MANAGER_PATH.read_text(encoding="utf-8")
    # Capture the base64 payload (group 1) so we can reconstruct the
    # full data URL for byte-for-byte comparison against the freshly
    # generated output. The ``\s*`` between ``=`` and the opening
    # quote accepts the multi-line layout used in the current source
    # (``const START_BEEP_WAV =\n\t"data:..."``).
    start_pattern = re.compile(
        r'const\s+START_BEEP_WAV\s*=\s*"data:audio/wav;base64,([A-Za-z0-9+/=]*)"\s*;',
        re.MULTILINE,
    )
    stop_pattern = re.compile(
        r'const\s+STOP_BEEP_WAV\s*=\s*"data:audio/wav;base64,([A-Za-z0-9+/=]*)"\s*;',
        re.MULTILINE,
    )
    start_match = start_pattern.search(text)
    stop_match = stop_pattern.search(text)
    if not start_match:
        raise RuntimeError(
            "Could not find START_BEEP_WAV constant in "
            "lib/sound-manager/beeps.ts — the source layout has changed; "
            "update generate_beeps.py."
        )
    if not stop_match:
        raise RuntimeError(
            "Could not find STOP_BEEP_WAV constant in "
            "lib/sound-manager/beeps.ts — the source layout has changed; "
            "update generate_beeps.py."
        )
    start_url = f"data:audio/wav;base64,{start_match.group(1)}"
    stop_url = f"data:audio/wav;base64,{stop_match.group(1)}"
    return start_url, stop_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Regression guard: exit 1 if the START and STOP URLs are "
            "identical OR if the constants committed in "
            "lib/sound-manager/beeps.ts do not match the freshly generated URLs."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the generated constants into lib/sound-manager/beeps.ts in place.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the printed URLs (useful with --write).",
    )
    args = parser.parse_args()

    start_url = generate_start_url()
    stop_url = generate_stop_url()

    if start_url == stop_url:
        print(
            "ERROR: START and STOP beeps are byte-for-byte identical.",
            file=sys.stderr,
        )
        return 1

    if args.check:
        # Regression guard: verify the constants committed to
        # lib/sound-manager/beeps.ts match the freshly-generated URLs and are
        # distinct from each other. The previous --check only verified
        # that the GENERATED URLs were distinct — it did NOT read the
        # source file, so a stale or accidentally-collapsed pair of
        # constants in lib/sound-manager/beeps.ts would pass the guard (false
        # assurance). This tighter check fails fast if the committed
        # constants drift from the canonical generator output.
        try:
            sm_start, sm_stop = _read_sound_manager_urls()
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print(
                "Hint: run `python scripts/build/generate_beeps.py --write` to regenerate the constants.",
                file=sys.stderr,
            )
            return 1
        if sm_start == sm_stop:
            print(
                "ERROR: lib/sound-manager/beeps.ts START_BEEP_WAV and "
                "STOP_BEEP_WAV are byte-for-byte identical — the regression "
                "this script exists to prevent has re-occurred in the source "
                "file.",
                file=sys.stderr,
            )
            print(
                "Hint: run `python scripts/build/generate_beeps.py --write` to regenerate the constants.",
                file=sys.stderr,
            )
            return 1
        if sm_start != start_url:
            print(
                "ERROR: lib/sound-manager/beeps.ts START_BEEP_WAV does not "
                "match the freshly generated URL — the committed constant "
                "has drifted from the canonical generator output.",
                file=sys.stderr,
            )
            print(
                "Hint: run `python scripts/build/generate_beeps.py --write` to regenerate the constants.",
                file=sys.stderr,
            )
            return 1
        if sm_stop != stop_url:
            print(
                "ERROR: lib/sound-manager/beeps.ts STOP_BEEP_WAV does not "
                "match the freshly generated URL — the committed constant "
                "has drifted from the canonical generator output.",
                file=sys.stderr,
            )
            print(
                "Hint: run `python scripts/build/generate_beeps.py --write` to regenerate the constants.",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.write:
        changed = _patch_sound_manager(start_url, stop_url)
        action = "updated" if changed else "already up-to-date"
        print(f"lib/sound-manager/beeps.ts: {action}", file=sys.stderr)

    if not args.quiet:
        print(f"START_BEEP_WAV = {start_url[:200]}...")
        print(f"STOP_BEEP_WAV  = {stop_url[:200]}...")
        print(f"START length = {len(start_url)} chars")
        print(f"STOP length  = {len(stop_url)} chars")
        print(f"First-200-char prefix identical? {start_url[:200] == stop_url[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
