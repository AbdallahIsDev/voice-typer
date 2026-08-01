"""Static-source tripwire tests pinning four previously-fixed invariants.

Each test class targets one finding from ``review.md`` and verifies that
the fix pattern is still present in the cited source file.  These are
*static-source* inspections (``Path.read_text()`` + substring checks)
rather than runtime tests — the runtime behaviour is already covered by
the dedicated test modules below.  The point of this file is to fail
*fast and loudly* if a future refactor reverts a fix without also
updating the dedicated tests:

- streaming-session TOCTOU in ``recording_controller._stop_impl`` —
  ``_stop_impl`` must call ``_cancel_streaming_session()`` (which uses
  the atomic ``pop_streaming_session()`` helper + public
  ``session.cancel()``) and must NOT poke the private
  ``session._cancel_event.set()``.  Runtime behaviour is pinned in
  ``tests/test_recording_controller_lifecycle_fixes.py``.
- ``useHotkeyCapture`` effect without a deps array — every
  ``useEffect(...)`` call in ``useHotkeyCapture.ts`` must close with a
  dependency array (``}, [...]);``), never a bare ``});``.  Runtime
  behaviour is pinned in
  ``voice_typer/client/src/renderer/src/__tests__/useHotkeyCapture_deps.test.tsx``.
- service-layer mic cache invalidation — ``DeviceManager`` must expose
  ``_service_cache_invalidator`` + ``set_service_cache_invalidator`` and
  ``_invalidate_device_cache`` must invoke the registered callback.
  Runtime behaviour is pinned in ``tests/test_device_manager.py``.
- name-based device resolution — ``DeviceManager._resolve_device`` must
  parse the compound ``"<index>|<name>|<host_api>"`` form, prefer
  ``find_microphone_by_name``, fall back to the saved index, and emit a
  one-time name-mismatch warning.  Runtime behaviour is pinned in
  ``tests/test_device_manager.py``.

The tests are intentionally cheap (file reads + substring checks) so
they can run on every CI job without spinning up PortAudio, the model
loader, or the Electron renderer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ──────────────────────────────────────────────────────────────────────────
# Source-file locator: resolve repo-relative paths from this test file.
# ──────────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECORDING_CONTROLLER = _REPO_ROOT / "voice_typer/server/recording_controller.py"
_DEVICE_MANAGER = _REPO_ROOT / "voice_typer/server/recording/device_manager.py"
_MICROPHONE_LIST = _REPO_ROOT / "voice_typer/server/server_platform/microphone_list.py"
_USE_HOTKEY_CAPTURE = _REPO_ROOT / "voice_typer/client/src/renderer/src/components/hotkey/useHotkeyCapture.ts"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.fail(f"source file not found: {path}")
    return path.read_text(encoding="utf-8")


# ─── streaming-session TOCTOU: _stop_impl uses the atomic cancel helper ──


class TestStopImplUsesAtomicStreamingCancel:
    """``_stop_impl`` must not poke the private ``_cancel_event`` attribute.

    Pins the streaming-session TOCTOU fix: the stop path must call
    ``_cancel_streaming_session()`` (which atomically pops the session
    and calls the public ``cancel()`` method), NOT
    ``session._cancel_event.set()`` (a private-attribute poke that
    bypasses the atomic pop and leaves the session reference dangling).
    """

    def test_stop_impl_calls_atomic_cancel_helper(self):
        src = _read(_RECORDING_CONTROLLER)
        # The helper invocation appears in the main transcription path
        # AND in the early-return paths (too-short audio, recorder.stop
        # exception). At least one occurrence inside _stop_impl is
        # required; the dedicated runtime tests pin the per-path
        # semantics.
        assert "_cancel_streaming_session()" in src, (
            "_stop_impl must call _cancel_streaming_session() to atomically pop + cancel the streaming session."
        )

    def test_stop_impl_does_not_poke_private_cancel_event(self):
        """No production code path in recording_controller may call
        ``session._cancel_event.set()`` — the attribute is private to
        ``StreamingTranscriptionSession`` and was the pre-fix contract
        that the TOCTOU window depended on.

        Comments documenting the pre-fix pattern are allowed (they
        explain WHY the helper exists); we only forbid executable
        attribute pokes by anchoring on ``.set()`` following
        ``_cancel_event`` without a leading ``#``.
        """
        src = _read(_RECORDING_CONTROLLER)
        offending = []
        for line_no, line in enumerate(src.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "_cancel_event" in line and ".set()" in line:
                offending.append((line_no, line.rstrip()))
        assert not offending, (
            "recording_controller.py must not poke the private "
            "_cancel_event attribute via .set(); found offending lines: "
            f"{offending}"
        )

    def test_pop_streaming_session_helper_exists(self):
        """The atomic get-and-clear helper must be defined on
        RecordingController so the cancel helper can call it under a
        single lock acquisition."""
        src = _read(_RECORDING_CONTROLLER)
        assert "def pop_streaming_session(" in src, (
            "pop_streaming_session() helper must exist to provide the "
            "atomic get-and-clear semantics required by the TOCTOU fix."
        )
        assert "session = self.pop_streaming_session()" in src, (
            "_cancel_streaming_session must call pop_streaming_session() "
            "(not the non-atomic get_streaming_session + set_streaming_session pair)."
        )
        assert "session.cancel()" in src, (
            "_cancel_streaming_session must invoke the public cancel() method "
            "on the popped session, not poke _cancel_event.set()."
        )


# ─── useHotkeyCapture: every useEffect has a deps array ──────────────────


class TestUseHotkeyCaptureEffectsHaveDepsArrays:
    """Every ``useEffect(...)`` call in ``useHotkeyCapture.ts`` must
    close with a dependency array.

    Pins the no-deps-array fix: the pre-fix code had a "track latest
    callbacks into refs after every render" effect that closed with a
    bare ``});`` (no deps array), causing it to run after every commit.
    The fix removed that effect entirely and made the handlers stable
    via ``useCallback``; this test ensures no future edit reintroduces
    a deps-less effect.
    """

    def test_no_useeffect_without_deps_array(self):
        src = _read(_USE_HOTKEY_CAPTURE)
        # Find every useEffect(() => { ... }); block. We can't easily
        # parse TS with regex, so we approximate: each useEffect call
        # must be followed (after its body) by ``}, [``,
        # ``}, []);`` or ``}, [...]);``. A bare ``});`` immediately
        # closing an effect body is the bug signature.
        #
        # Strategy: split on ``useEffect(()`` and inspect each chunk's
        # closing sequence. Each effect body ends with ``\t},`` followed
        # by either ``[`` (deps array) or ``)`` (no deps — bug).
        effect_starts = [m.start() for m in re.finditer(r"useEffect\(\(\)", src)]
        assert effect_starts, "expected at least one useEffect(() => ...) call"
        offenders: list[str] = []
        for start in effect_starts:
            # Take a generous slice after the useEffect(() => token to
            # capture the body + closing sequence.
            chunk = src[start : start + 4000]
            # The closing pattern we want: ``\n\t}, [...]);`` (deps array).
            # The buggy pattern: ``\n\t});`` (no deps).
            # We check the FIRST occurrence of either pattern after the
            # effect opening.
            deps_close = re.search(r"\n\s*\},\s*\[", chunk)
            bare_close = re.search(r"\n\s*\}\s*\);", chunk)
            if bare_close and (not deps_close or bare_close.start() < deps_close.start()):
                # Found a bare ``});`` before any ``}, [`` — possible
                # no-deps effect.  Capture a short snippet for the error
                # message.
                snippet = chunk[max(0, bare_close.start() - 80) : bare_close.end() + 20]
                offenders.append(snippet)
        assert not offenders, (
            "useHotkeyCapture.ts must not contain a useEffect without a "
            "dependency array. Possible offending effect(s):\n" + "\n---\n".join(offenders)
        )

    def test_handlers_are_usecallback_stable(self):
        """``handleKeyDown`` and ``handleKeyUp`` must be wrapped in
        ``useCallback(...)`` so their identity is stable across renders
        (the always-attached listener effect's deps array depends on
        this stability)."""
        src = _read(_USE_HOTKEY_CAPTURE)
        for handler in ("handleKeyDown", "handleKeyUp"):
            pattern = rf"const {handler} = useCallback\("
            assert re.search(pattern, src), (
                f"{handler} must be defined via useCallback so its identity "
                "is stable across renders (the always-attached keyboard "
                "listener effect depends on this)."
            )


# ─── service-layer mic cache invalidator wiring ──────────────────────────


class TestServiceLayerMicCacheInvalidator:
    """``DeviceManager`` must expose a service-cache-invalidator hook
    and ``_invalidate_device_cache`` must invoke it.

    Pins the fix for the OS-watcher-doesn't-invalidate-service-cache
    bug: pre-fix, ``MicrophoneDeviceWatcher`` invalidated only
    ``DeviceManager._device_list_cache``, leaving the service-layer
    5s-TTL cache stale for up to 5s after a hot-plug event. The fix
    adds a callback hook that ``ipc_server`` wires to
    ``service.refresh_microphones(force=True)``.
    """

    def test_service_cache_invalidator_attribute_exists(self):
        src = _read(_DEVICE_MANAGER)
        assert "_service_cache_invalidator" in src, (
            "DeviceManager must declare _service_cache_invalidator so the "
            "service layer can register a hot-plug invalidation callback."
        )

    def test_set_service_cache_invalidator_method_exists(self):
        src = _read(_DEVICE_MANAGER)
        assert "def set_service_cache_invalidator(" in src, (
            "DeviceManager must expose set_service_cache_invalidator(callback) "
            "so ipc_server can wire the service-layer cache invalidator."
        )

    def test_invalidate_device_cache_invokes_service_callback(self):
        src = _read(_DEVICE_MANAGER)
        # The body of _invalidate_device_cache must read the callback
        # into a local and call it under try/except (best-effort).
        assert "service_cb = self._service_cache_invalidator" in src, (
            "_invalidate_device_cache must read the service callback into a "
            "local before invoking it (so a concurrent unregister doesn't "
            "race the call)."
        )
        assert "service_cb()" in src, (
            "_invalidate_device_cache must invoke the registered service "
            "callback so the service-layer mic cache is invalidated on "
            "OS-reported hot-plug events."
        )


# ─── name-based device resolution (compound form) ────────────────────────


class TestNameBasedDeviceResolution:
    """``_resolve_device`` must parse the compound
    ``"<index>|<name>|<host_api>"`` form, prefer name-based resolution
    via ``find_microphone_by_name``, fall back to the saved index, and
    emit a one-time name-mismatch warning.

    Pins the fix for the device-index-as-persistent-identifier bug:
    pre-fix, ``config.microphone`` stored only a PortAudio device
    index, which shifts whenever devices are added or removed. The
    fix stores the device name alongside the index so the resolver can
    re-find the original physical device by name after renumbering.
    """

    def test_resolve_device_parses_compound_form(self):
        src = _read(_DEVICE_MANAGER)
        # The guard may be expressed as either ``"|" in mic`` (positive)
        # or ``"|" not in mic`` (negative early-return). Both are valid
        # as long as the compound-form detection happens before the
        # split.
        assert '"|"' in src and "mic" in src, (
            "_resolve_device must check for the '|' separator in config.microphone to detect the compound form."
        )
        assert 'mic.split("|"' in src, (
            "_resolve_device must split the compound form on '|' to "
            "extract the saved index, name, and host_api components."
        )

    def test_resolve_device_prefers_name_lookup(self):
        src = _read(_DEVICE_MANAGER)
        assert "find_microphone_by_name" in src, (
            "_resolve_device must call find_microphone_by_name() to "
            "resolve the device by its stable name (survives hot-swap "
            "renumbering) before falling back to the saved index."
        )

    def test_find_microphone_by_name_exists_in_microphone_list(self):
        src = _read(_MICROPHONE_LIST)
        assert "def find_microphone_by_name(" in src, (
            "microphone_list.py must define find_microphone_by_name() so "
            "DeviceManager._resolve_device can resolve a device by name."
        )

    def test_resolve_device_emits_one_time_mismatch_warning(self):
        src = _read(_DEVICE_MANAGER)
        # The one-shot flag prevents the warning from firing on every
        # call after the first mismatch detection.
        assert "_device_name_mismatch_warned" in src, (
            "DeviceManager must track a one-shot flag so the name-mismatch "
            "warning fires at most once per instance (avoids log spam on "
            "every _resolve_device call)."
        )
        # The warning text must mention "now points to" so users can
        # recognise the renumbering scenario.
        assert "now points to" in src, (
            "_resolve_device must emit a warning mentioning 'now points to' "
            "when the saved index resolves to a differently-named device, "
            "so the user knows to re-select the microphone in Settings."
        )

    def test_resolve_device_falls_back_to_saved_index(self):
        src = _read(_DEVICE_MANAGER)
        # The fallback path returns the saved_index (an int) when the
        # name lookup fails. The compound form splits into at least
        # saved_index_str + saved_name; saved_index is the int parse.
        assert "saved_index" in src, (
            "_resolve_device must keep the parsed saved_index so it can "
            "fall back to it when name-based resolution fails."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
