"""
Static-source tripwire tests pinning four previously-fixed invariants.
``session.cancel()``) and must NOT poke the private
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECORDING_CONTROLLER = _REPO_ROOT / "voice_typer/server/recording_controller.py"
_DEVICE_MANAGER = _REPO_ROOT / "voice_typer/server/recording/device_manager.py"
_MICROPHONE_LIST = _REPO_ROOT / "voice_typer/server/server_platform/microphone_list.py"
_USE_HOTKEY_CAPTURE = _REPO_ROOT / "voice_typer/client/src/renderer/src/components/hotkey/useHotkeyCapture.ts"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.fail(f"source file not found: {path}")
    return path.read_text(encoding="utf-8")


class TestStopImplUsesAtomicStreamingCancel:
    """
    ``_stop_impl`` must not poke the private ``_cancel_event`` attribute.
    Pins the streaming-session TOCTOU fix: the stop path must call
    """

    def test_stop_impl_calls_atomic_cancel_helper(self):
        src = _read(_RECORDING_CONTROLLER)
        assert "_cancel_streaming_session()" in src, (
            "_stop_impl must call _cancel_streaming_session() to atomically pop + cancel the streaming session."
        )

    def test_stop_impl_does_not_poke_private_cancel_event(self):
        """No production code path in recording_controller may call"""
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
        """single lock acquisition."""
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


class TestUseHotkeyCaptureEffectsHaveDepsArrays:
    """Every ``useEffect(...)`` call in ``useHotkeyCapture.ts`` must"""

    def test_no_useeffect_without_deps_array(self):
        src = _read(_USE_HOTKEY_CAPTURE)
        # Find every useEffect(() => { ... }); block. We can't easily
        effect_starts = [m.start() for m in re.finditer(r"useEffect\(\(\)", src)]
        assert effect_starts, "expected at least one useEffect(() => ...) call"
        offenders: list[str] = []
        for start in effect_starts:
            chunk = src[start : start + 4000]
            # The closing pattern we want: ``\n\t}, [...]);`` (deps array).
            deps_close = re.search(r"\n\s*\},\s*\[", chunk)
            bare_close = re.search(r"\n\s*\}\s*\);", chunk)
            if bare_close and (not deps_close or bare_close.start() < deps_close.start()):
                # Found a bare ``});`` before any ``}, [``, possible
                snippet = chunk[max(0, bare_close.start() - 80) : bare_close.end() + 20]
                offenders.append(snippet)
        assert not offenders, (
            "useHotkeyCapture.ts must not contain a useEffect without a "
            "dependency array. Possible offending effect(s):\n" + "\n---\n".join(offenders)
        )

    def test_handlers_are_usecallback_stable(self):
        """``useCallback(...)`` so their identity is stable across renders"""
        src = _read(_USE_HOTKEY_CAPTURE)
        for handler in ("handleKeyDown", "handleKeyUp"):
            pattern = rf"const {handler} = useCallback\("
            assert re.search(pattern, src), (
                f"{handler} must be defined via useCallback so its identity "
                "is stable across renders (the always-attached keyboard "
                "listener effect depends on this)."
            )


class TestServiceLayerMicCacheInvalidator:
    """``DeviceManager`` must expose a service-cache-invalidator hook"""

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


class TestNameBasedDeviceResolution:
    """``_resolve_device`` delegates to the canonical id resolver."""

    def test_resolve_device_delegates_to_canonical_resolver(self):
        src = _read(_DEVICE_MANAGER)
        assert "resolve_mic_id_to_device_index" in src, (
            "_resolve_device must delegate to resolve_mic_id_to_device_index()."
        )

    def test_resolve_device_has_no_parallel_ladder(self):
        src = _read(_DEVICE_MANAGER)
        assert "find_microphone_by_name" not in src, "_resolve_device must not keep a parallel name-lookup ladder."
        assert "saved_index" not in src, "_resolve_device must not fall back to a stale saved index."

    def test_find_microphone_by_name_exists_in_microphone_list(self):
        src = _read(_MICROPHONE_LIST)
        assert "def find_microphone_by_name(" in src, (
            "microphone_list.py must define find_microphone_by_name() so "
            "legacy compound ids keep resolving through the canonical path."
        )

    def test_resolve_device_returns_none_when_unresolvable(self):
        src = _read(_DEVICE_MANAGER)
        assert "return None" in src, (
            "_resolve_device must return None (System Default) when the canonical resolver cannot map the persisted id."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
