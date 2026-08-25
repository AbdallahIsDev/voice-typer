"""direct unit tests for the
``permissions.json`` entry added to the diagnostic bundle by
``voice_typer/server/diagnostics_export.py``.

Pre-fix, the diagnostic bundle zip (created by
``create_diagnostic_bundle``) NEVER imported
``voice_typer.server.permissions`` and never called
``check_keyboard_permission()`` / ``check_microphone_permission()``.
Support engineers reading a bug-report zip had to ask the user to run
``--status`` manually to gather the OS-level permission picture.

 adds a ``permissions.json`` entry to the bundle that contains:
  - ``keyboard_permission_state`` (from ``check_keyboard_permission().value``)
  - ``microphone_permission_state`` (from
    ``check_microphone_permission().value``)
  - ``pyobjc_available`` (from ``_is_pyobjc_available()``)
  - On Linux only:
      * ``install_manifest`` (parsed contents of
        ``/var/lib/voice-typer/permissions-manifest.json`` if it exists)
      * ``dev_input_event0_readable`` (``os.access(..., os.R_OK)``)

These tests pin the schema so a future refactor that drops any of the
required keys will fail loudly. The probes run against the real host
platform (Linux in the sandbox); on Linux ``check_keyboard_permission``
returns ``"granted"`` / ``"denied"`` / ``"unknown"`` depending on the
input-group membership + device readability, so we don't pin the value
— we just assert the key is present and well-typed.
"""

from __future__ import annotations

import json
import zipfile

import pytest
from voice_typer.server import diagnostics_export

# ── Shared fixtures (mirrors tests/test_diagnostics_export.py) ────────


@pytest.fixture
def recovery_dir(tmp_config_dir):
    """Point ``voice_typer.server.config._config_dir`` at a tmp_path."""
    return tmp_config_dir


@pytest.fixture
def recovery(recovery_dir):
    """Build a real ``CrashRecovery`` instance backed by the tmp
    config dir."""
    from voice_typer.server.crash_recovery import CrashRecovery

    return CrashRecovery(config_dir=recovery_dir)


# ── Schema validation (required keys present + well-typed) ────────────


class TestPermissionsJsonSchema:
    """The ``permissions.json`` entry MUST contain the documented keys
    so support engineers can rely on a stable schema:

      - ``keyboard_permission_state`` (str — one of ``"granted"`` /
        ``"denied"`` / ``"unknown"`` / ``"error"``)
      - ``microphone_permission_state`` (str — one of ``"granted"`` /
        ``"denied"`` / ``"prompt"`` / ``"unknown"``)
      - ``pyobjc_available`` (bool)

    Linux-only keys (``install_manifest`` /
    ``dev_input_event0_readable``) are gated on platform and are
    checked in :class:`TestLinuxOnlyKeys`.
    """

    def test_bundle_contains_permissions_json(self, recovery) -> None:
        """The diagnostic bundle MUST include a ``permissions.json``
        entry — its absence means the  addition was reverted
        or the bundle-creation code path skipped it."""
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None, "create_diagnostic_bundle must return a path, not None"

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()

        assert "permissions.json" in names, f"diagnostic bundle missing 'permissions.json' entry; got names: {names}"

    def test_permissions_json_has_required_keys(self, recovery) -> None:
        """``permissions.json`` MUST contain the three required keys
        (``keyboard_permission_state`` / ``microphone_permission_state``
        / ``pyobjc_available``). The pre-fix bundle had NONE of these
        — support engineers had to ask the user to run ``--status``
        manually."""
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        # Defensive: an "error" key on the root means the probe
        # itself failed (the bundle still ships the entry with an
        # error payload so support knows why permission data is
        # missing). The required keys should still be present on
        # the success path; on the failure path, only ``error`` is
        # required. We assert the success-path contract here.
        if "error" not in data:
            for key in ("keyboard_permission_state", "microphone_permission_state", "pyobjc_available"):
                assert key in data, f"permissions.json missing required key {key!r}; got: {data!r}"

    def test_keyboard_permission_state_is_well_typed(self, recovery) -> None:
        """``keyboard_permission_state`` MUST be a string matching one
        of the ``PermissionState`` enum values (``"granted"`` /
        ``"denied"`` / ``"unknown"`` / ``"error"``)."""
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        if "error" not in data:
            kb_state = data.get("keyboard_permission_state")
            assert isinstance(kb_state, str), (
                f"keyboard_permission_state must be a str; got {type(kb_state).__name__}: {kb_state!r}"
            )
            assert kb_state in {"granted", "denied", "unknown", "error"}, (
                f"keyboard_permission_state must be one of granted/denied/unknown/error; got: {kb_state!r}"
            )

    def test_microphone_permission_state_is_well_typed(self, recovery) -> None:
        """``microphone_permission_state`` MUST be a string matching
        one of the ``MicrophonePermissionState`` enum values
        (``"granted"`` / ``"denied"`` / ``"prompt"`` / ``"unknown"``)."""
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        if "error" not in data:
            mic_state = data.get("microphone_permission_state")
            assert isinstance(mic_state, str), (
                f"microphone_permission_state must be a str; got {type(mic_state).__name__}: {mic_state!r}"
            )
            assert mic_state in {"granted", "denied", "prompt", "unknown"}, (
                f"microphone_permission_state must be one of granted/denied/prompt/unknown; got: {mic_state!r}"
            )

    def test_pyobjc_available_is_bool(self, recovery) -> None:
        """``pyobjc_available`` MUST be a bool (the cached result of
        ``_is_pyobjc_available()`` — ``False`` on Linux/CI where
        pyobjc isn't installed, ``True`` on macOS with pyobjc)."""
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        if "error" not in data:
            pyobjc = data.get("pyobjc_available")
            assert isinstance(pyobjc, bool), f"pyobjc_available must be a bool; got {type(pyobjc).__name__}: {pyobjc!r}"


# ── Linux-only keys (install_manifest + dev_input_event0_readable) ───


class TestLinuxOnlyKeys:
    """On Linux, the ``permissions.json`` entry MUST additionally
    include:

      - ``install_manifest``: parsed contents of
        ``/var/lib/voice-typer/permissions-manifest.json`` if the file
        exists, or ``None`` if it doesn't (e.g. user never ran
        ``install_permissions.py``).
      - ``dev_input_event0_readable``: ``os.access("/dev/input/event0",
        os.R_OK)`` — a ground-truth readability check that support
        engineers can correlate against the
        ``keyboard_permission_state`` probe result.

    These keys are gated on ``sys.platform.startswith("linux")`` — on
    macOS / Windows they're absent (support infers "not applicable"
    from the platform field in ``system_info.txt``). The sandbox is
    Linux, so we assert the keys are present here; the macOS/Windows
    branch is exercised only in CI on those platforms.
    """

    @pytest.mark.skipif(
        not __import__("sys").platform.startswith("linux"),
        reason="Linux-only keys are gated on sys.platform",
    )
    def test_linux_bundle_includes_install_manifest_key(self, recovery) -> None:
        """On Linux, ``permissions.json`` MUST include the
        ``install_manifest`` key (either the parsed JSON, or ``None``
        if the manifest file doesn't exist)."""
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        if "error" not in data:
            assert "install_manifest" in data, (
                f"Linux permissions.json must include 'install_manifest' key; got: {data!r}"
            )
            # When the manifest file doesn't exist (typical sandbox /
            # CI case), the value is None. When it exists, it's the
            # parsed JSON dict (or an ``{"error": ...}`` dict if the
            # JSON parse failed). Either is acceptable.
            assert data["install_manifest"] is None or isinstance(data["install_manifest"], dict), (
                f"install_manifest must be None or a dict; got: {data['install_manifest']!r}"
            )

    @pytest.mark.skipif(
        not __import__("sys").platform.startswith("linux"),
        reason="Linux-only keys are gated on sys.platform",
    )
    def test_linux_bundle_includes_dev_input_event0_readable(self, recovery) -> None:
        """On Linux, ``permissions.json`` MUST include the
        ``dev_input_event0_readable`` key — a raw bool from
        ``os.access("/dev/input/event0", os.R_OK)`` that lets support
        correlate the probe result against the filesystem state."""
        import os as _os

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        if "error" not in data:
            assert "dev_input_event0_readable" in data, (
                f"Linux permissions.json must include 'dev_input_event0_readable' key; got: {data!r}"
            )
            assert isinstance(data["dev_input_event0_readable"], bool), (
                f"dev_input_event0_readable must be a bool; got: {data['dev_input_event0_readable']!r}"
            )
            # Sanity: the value must match a fresh os.access call.
            expected = _os.access("/dev/input/event0", _os.R_OK)
            assert data["dev_input_event0_readable"] == expected, (
                f"dev_input_event0_readable value doesn't match a fresh os.access call; "
                f"bundle={data['dev_input_event0_readable']!r}, fresh={expected!r}"
            )


# ── Probe-failure resilience ──────────────────────────────────────────


class TestProbeFailureResilience:
    """If the permissions probe raises (e.g. ``check_keyboard_permission``
    raises because the platform probe blew up), the bundle MUST still
    be created with an ``error`` key in ``permissions.json`` so
    support engineers know why permission data is missing — the probe
    failure MUST NOT abort the entire bundle creation.
    """

    def test_bundle_still_created_when_keyboard_probe_raises(self, recovery, monkeypatch) -> None:
        """If ``check_keyboard_permission`` raises, the bundle must
        still be created and ``permissions.json`` must contain an
        ``error`` key (rather than the schema keys)."""

        def _raise():
            raise RuntimeError("simulated probe failure")

        # Patch at the source module so the lazy import inside
        # ``create_diagnostic_bundle`` picks up the broken function.
        from voice_typer.server import permissions as _perm_mod

        monkeypatch.setattr(_perm_mod, "check_keyboard_permission", _raise)

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None, (
            "create_diagnostic_bundle must NOT return None when the permissions probe raises — "
            "the probe failure must be caught and surfaced as an error key in permissions.json"
        )

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        assert "error" in data, f"permissions.json must contain an 'error' key when the probe raises; got: {data!r}"
        assert "simulated probe failure" in str(data["error"]), (
            f"permissions.json error must contain the probe failure message; got: {data['error']!r}"
        )


# ── install_manifest contents (when the file exists) ─────────────────


class TestInstallManifestContents:
    """When ``/var/lib/voice-typer/permissions-manifest.json`` exists,
    the bundle's ``install_manifest`` key MUST contain the parsed JSON
    contents. When the file doesn't exist, it MUST be ``None``.

    The manifest path is hard-coded to
    ``/var/lib/voice-typer/permissions-manifest.json`` (written by
    ``scripts/linux/install_permissions.py``). We can't easily test
    the "exists" branch without root + a real install, so we test
    only the "doesn't exist" branch here (the typical sandbox case).
    The "exists" branch is exercised by the integration tests in
    ``tests/tauri/mig17/test_native_key_listener_linux.py``.
    """

    @pytest.mark.skipif(
        not __import__("sys").platform.startswith("linux"),
        reason="Linux-only manifest path",
    )
    def test_install_manifest_is_none_when_file_absent(self, recovery) -> None:
        """When ``/var/lib/voice-typer/permissions-manifest.json``
        doesn't exist (typical sandbox / CI case — only written after
        a real ``install_permissions.py`` run), the bundle's
        ``install_manifest`` value MUST be ``None`` (NOT an error —
        the file's absence is a legitimate state meaning "user hasn't
        run the installer yet")."""
        # Skip if the manifest file actually exists on this host
        # (e.g. a developer machine with a real install).
        from pathlib import Path as _Path

        if _Path("/var/lib/voice-typer/permissions-manifest.json").is_file():
            pytest.skip("manifest file exists on this host — can't test the 'absent' branch")

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("permissions.json"))

        if "error" not in data:
            assert data.get("install_manifest") is None, (
                f"install_manifest must be None when the file is absent; got: {data.get('install_manifest')!r}"
            )
