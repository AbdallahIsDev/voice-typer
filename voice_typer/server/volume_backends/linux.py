"""Linux volume backend — pactl → wpctl → amixer.

Extracted from the original ``voice_typer/server/volume_backends.py``
monolith per PVT-24.  See ``voice_typer/server/volume_backends/__init__.py``
for the package-level docstring and re-exports.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState

log = logging.getLogger(__name__)


# Smart-duck polling on Linux is expensive: every ``is_speaker_active()``
# call spawns ``pactl list sink-inputs`` (~50–100 ms per invocation on a
# typical desktop).  At the default 500 ms cadence inherited from the
# base class, that's 10–20% CPU on one core just for smart-duck — plus
# noticeable battery drain on laptops.  Advertising 1500 ms as the
# minimum safe cadence keeps the monitor responsive (catches audio
# start within ~1.5 s) while cutting the CPU/battery cost 3×.  Users
# who explicitly set a faster ``volume_duck_smart_poll_interval_ms``
# config value are still respected — ``VolumeDucker.initialize`` uses
# ``max(user_value, min_poll_interval_ms)`` so the monitor never polls
# faster than the backend can handle but the user's explicit slower
# value is also honoured.
_LINUX_MIN_SMART_DUCK_POLL_MS = 1500


class LinuxVolumeBackend(VolumeBackend):
    """Linux volume control with automatic backend detection.

    Detection order:
      1. ``pactl`` — works on both PulseAudio and PipeWire (via compat layer).
         Handles ~95% of desktop Linux installs.
      2. ``wpctl`` — WirePlumber CLI, native to PipeWire-only systems
         that dropped the PulseAudio compat layer.
      3. ``amixer`` — ALSA hardware mixer, the last-resort fallback for
         bare ALSA systems (Raspbian Lite, minimal servers, embedded).

    Per-session ducking is theoretically possible via
    ``pactl set-sink-input-volume`` but enumeration is fragile, so
    :attr:`supports_per_session` is ``False`` for v1.
    """

    def __init__(self) -> None:
        self._tool: str | None = None

    @property
    def name(self) -> str:
        return f"linux ({self._tool})" if self._tool else "linux (uninitialised)"

    @property
    def supports_per_session(self) -> bool:
        return False

    @property
    def _set_linear_is_subprocess(self) -> bool:
        """Linux backends always spawn a subprocess (pactl/wpctl/amixer).

        ``fade_to`` collapses to a single :meth:`set_linear` call so we
        don't fire 10 sequential ``pactl`` invocations (~50 ms each →
        500 ms total + audible stepping between steps).
        """
        return True

    @property
    def min_poll_interval_ms(self) -> int:
        """1500 ms — Linux smart-duck polls spawn ``pactl list sink-inputs``
        (~50–100 ms each).  At the default 500 ms cadence the monitor
        would burn 10–20% CPU on one core.  1500 ms keeps the monitor
        responsive (audio-start detected within ~1.5 s) while cutting
        the per-poll cost 3×.  See :data:`_LINUX_MIN_SMART_DUCK_POLL_MS`.
        """
        return _LINUX_MIN_SMART_DUCK_POLL_MS

    def initialize(self) -> bool:
        if self._tool is not None:
            return True
        for tool in ("pactl", "wpctl", "amixer"):
            if shutil.which(tool):
                self._tool = tool
                log.info("[VOLUME-LINUX] Using %s", tool)
                return True
        log.info("[VOLUME-LINUX] No volume tool found (pactl/wpctl/amixer)")
        return False

    def get_state(self) -> VolumeState | None:
        if self._tool == "pactl":
            return self._pactl_get()
        if self._tool == "wpctl":
            return self._wpctl_get()
        if self._tool == "amixer":
            return self._amixer_get()
        return None

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._tool == "pactl":
            return self._pactl_set(level, muted)
        if self._tool == "wpctl":
            return self._wpctl_set(level, muted)
        if self._tool == "amixer":
            return self._amixer_set(level, muted)
        return False

    def is_speaker_active(self) -> bool:
        """Return ``True`` if audio is currently playing on the default sink.

        Per-tool implementation:

        - **pactl**: ``pactl list sink-inputs`` — if any sink-input has
          ``State: running``, audio is being rendered.  Works on both
          PulseAudio and PipeWire (via the PulseAudio compat layer).
        - **wpctl**: PipeWire's ``pw-top`` would give per-client
          activity, but it's heavy.  Instead we try ``pactl list
          sink-inputs`` first (PipeWire ships the PulseAudio compat
          layer on most distros); if that fails, fall back to checking
          ``/proc/asound`` for ALSA-level activity.
        - **amixer (ALSA-only)**: scan
          ``/proc/asound/card*/pcm0p/sub*/status`` for
          ``state: RUNNING``.  This is the kernel-level signal that an
          audio stream is actively being rendered.  Works on bare ALSA
          systems without a sound server.

        Returns ``True`` (duck anyway) on any error so we never
        silently skip ducking when we should.
        """
        if self._tool == "pactl" or self._tool == "wpctl":
            # Try pactl first (works on PulseAudio + PipeWire compat).
            # For wpctl-only systems without pactl, _run will return None
            # and we fall through to the ALSA procfs check.
            out = self._run(["pactl", "list", "sink-inputs"], timeout=1.5)
            if out is not None:
                # Output contains blocks like:
                #   Sink Input #42
                #       State: running
                #       ...
                # We look for any "State: running" or "State: corked"
                # (corked = temporarily paused, but the stream exists).
                # Only "running" means audio is actually being produced.
                return "State: running" in out
            # pactl not available (wpctl-only PipeWire) — fall through
            # to the ALSA procfs check below.
        if self._tool == "amixer" or self._tool == "wpctl":
            # ALSA procfs fallback: scan all cards' playback substreams
            # for "state: RUNNING".  This is the kernel-level signal.
            return self._alsa_is_playing()
        return True  # unknown tool — duck to be safe

    def _alsa_is_playing(self) -> bool:
        """Check /proc/asound for any actively-rendering PCM substream."""
        try:
            # PVT-24 (test compat): look up ``Path`` via the package
            # namespace so tests that do
            # ``monkeypatch.setattr(volume_backends, "Path", fake_path)``
            # (see ``tests/test_smart_duck.py::TestLinuxIsSpeakerActive``)
            # continue to intercept ``Path("/proc/asound")`` lookups after
            # the split.  Before PVT-24, ``Path`` was a module global on
            # the single ``volume_backends.py`` module, so patching the
            # module's ``Path`` attribute was sufficient.  After the
            # split, ``_alsa_is_playing`` lives in ``linux.py`` and would
            # otherwise look up ``Path`` from ``linux.py``'s own globals
            # (bypassing the patch).  Routing through the package keeps
            # the patches effective without requiring test changes.
            from voice_typer.server import volume_backends as _vb_pkg

            Path = _vb_pkg.Path  # noqa: N806
            asound = Path("/proc/asound")
            if not asound.exists():
                return True  # not Linux? — duck to be safe
            for card_dir in asound.iterdir():
                if not card_dir.name.startswith("card"):
                    continue
                # Playback substreams live under pcm*p/ (the 'p' suffix
                # means playback; 'c' means capture).  Each substream
                # has a `status` file that contains "state: RUNNING"
                # when audio is being rendered.
                for pcm_dir in card_dir.glob("pcm*p"):
                    for sub in pcm_dir.glob("sub*"):
                        status_file = sub / "status"
                        if not status_file.exists():
                            continue
                        try:
                            content = status_file.read_text()
                            if "state: RUNNING" in content:
                                return True
                        except (OSError, PermissionError):
                            continue
            return False  # no running substreams found
        except Exception as exc:
            log.debug("[VOLUME-LINUX] _alsa_is_playing failed: %s", exc)
            return True  # safe default

    # ── pactl (PulseAudio / PipeWire compat) ────────────────────────

    def _run(self, cmd: list[str], timeout: float = 2.0) -> str | None:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                log.debug("[VOLUME-LINUX] %s error: %s", cmd[0], result.stderr.strip())
                return None
            return result.stdout.strip()
        except Exception as exc:
            log.debug("[VOLUME-LINUX] %s failed: %s", cmd[0], exc)
            return None

    def _pactl_get(self) -> VolumeState | None:
        # XV-56: ``pactl get-sink-volume`` and ``pactl get-sink-mute`` are
        # independent queries — run them in parallel via a 2-worker
        # ``ThreadPoolExecutor`` so the per-call latency (~100 ms each on
        # a cold pulseaudio daemon) overlaps instead of stacking.  Total
        # ``get_state`` cost drops from ~200 ms to ~100 ms, halving the
        # duck/restore latency on Linux.  ``ThreadPoolExecutor`` is used
        # (rather than ``asyncio``) because the caller (``VolumeDucker``)
        # is synchronous; the threads block on ``subprocess.run`` which
        # releases the GIL while waiting on the pipe, so this does not
        # starve other Python threads.
        with ThreadPoolExecutor(max_workers=2) as pool:
            vol_future = pool.submit(self._run, ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            mute_future = pool.submit(self._run, ["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            out = vol_future.result()
            mute_out = mute_future.result()
        if not out:
            return None
        # Output: "Volume: front-left: 65536 / 100% / 0.00 dB,   front-right: ..."
        match = re.search(r"(\d+)%", out)
        if not match:
            return None
        vol = int(match.group(1)) / 100.0
        muted = mute_out is not None and "yes" in mute_out.lower()
        return VolumeState(linear=vol, muted=muted)

    def _pactl_set(self, level: float, muted: bool | None) -> bool:
        pct = int(level * 100)
        ok = self._run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"]) is not None
        if muted is not None:
            mute_val = "1" if muted else "0"
            self._run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", mute_val])
        return ok

    # ── wpctl (WirePlumber / PipeWire native) ───────────────────────

    def _wpctl_get(self) -> VolumeState | None:
        out = self._run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        if not out:
            return None
        # Output: "Volume: 0.50" or "Volume: 0.50 [MUTED]"
        match = re.search(r"Volume:\s*([\d.]+)", out)
        if not match:
            return None
        vol = float(match.group(1))
        muted = "[MUTED]" in out.upper()
        return VolumeState(linear=max(0.0, min(1.0, vol)), muted=muted)

    def _wpctl_set(self, level: float, muted: bool | None) -> bool:
        ok = self._run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level:.2f}"]) is not None
        if muted is not None:
            mute_cmd = "mute" if muted else "unmute"
            self._run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", mute_cmd])
        return ok

    # ── amixer (ALSA fallback) ──────────────────────────────────────

    def _amixer_get(self) -> VolumeState | None:
        out = self._run(["amixer", "-D", "default", "sget", "Master"])
        if not out:
            return None
        # Output: "  Mono: Playback 50% [50%] [-6.00dB] [on]"
        match = re.search(r"\[(\d+)%\]", out)
        if not match:
            return None
        vol = int(match.group(1)) / 100.0
        muted = "[off]" in out.lower()
        return VolumeState(linear=vol, muted=muted)

    def _amixer_set(self, level: float, muted: bool | None) -> bool:
        pct = int(level * 100)
        ok = self._run(["amixer", "-D", "default", "sset", "Master", f"{pct}%"]) is not None
        if muted is not None:
            mute_val = "mute" if muted else "unmute"
            self._run(["amixer", "-D", "default", "sset", "Master", mute_val])
        return ok
