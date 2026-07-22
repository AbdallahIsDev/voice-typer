"""Regression test for CR-39: diagnostic bundle must NOT include transcription text.

The "Export diagnostics" button (``CrashRecovery.create_diagnostic_bundle``)
packages a zip file that users routinely attach to bug reports.  Previously
the ``crash_recovery.json`` entry inside that zip dumped the full
``self._entries`` list verbatim — including the ``text`` field of every
recovery entry, i.e. the user's transcribed speech.

CR-39 fixes this by emitting only metadata (count + per-entry timestamp /
pasted flag / text_length) so support engineers can see *that* an entry
existed without exposing what was said.

These tests pin the redaction so a future refactor that re-introduces the
leak will fail loudly.
"""

from __future__ import annotations

import json
import zipfile

import pytest


@pytest.fixture
def recovery_dir(tmp_path, monkeypatch):
    """Point ``voice_typer.server.config._config_dir`` at a tmp_path.

    ``CrashRecovery.create_diagnostic_bundle`` looks up the config dir via
    ``_config_dir()`` (not the instance's ``self._path``) when deciding
    where to write the bundle zip — so we have to monkeypatch the global.
    The same pattern is used in ``tests/test_crash_recovery.py``.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


class TestDiagnosticBundleNoPII:
    """CR-39: the diagnostic bundle must NOT include transcription text."""

    def test_bundle_omits_transcription_text(self, recovery_dir):
        """create_diagnostic_bundle must NOT include the 'text' field of
        recovery entries in the bundle's ``crash_recovery.json``.

        Regression: pre-CR-39 the entry dict was dumped verbatim, leaking
        the user's transcribed speech (potentially containing passwords,
        medical info, etc.) into bug-report attachments.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        # Add recovery entries with sensitive text. If any of these
        # strings survive into the bundle, the test fails.
        cr.add("my secret password is hunter2")
        cr.add("patient John Doe has diabetes")

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None, "create_diagnostic_bundle must return a path, not None"

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            assert "crash_recovery.json" in names, "diagnostic bundle must include a crash_recovery.json entry"
            crash_recovery_json = zf.read("crash_recovery.json").decode("utf-8")

        data = json.loads(crash_recovery_json)

        # The metadata envelope must be present.
        assert data.get("count") == 2, f"expected count=2 in crash_recovery.json, got: {data!r}"
        entries = data.get("entries", [])
        assert len(entries) == 2, f"expected 2 metadata entries, got: {entries!r}"

        # Each entry must contain ONLY timestamp / pasted / text_length —
        # never the raw ``text`` field.  We also assert the sensitive
        # strings don't appear anywhere in the JSON (defence in depth
        # against a future field rename that re-introduces the leak).
        for entry in entries:
            assert "text" not in entry, f"'text' field present in diagnostic entry — PII leak: {entry!r}"
            assert "hunter2" not in json.dumps(entry), f"secret value 'hunter2' leaked into entry: {entry!r}"
            assert "John Doe" not in json.dumps(entry), f"PII 'John Doe' leaked into entry: {entry!r}"
            # Metadata fields must be present and well-typed.
            assert "timestamp" in entry, f"timestamp missing from entry: {entry!r}"
            assert "pasted" in entry, f"pasted flag missing from entry: {entry!r}"
            assert "text_length" in entry, f"text_length missing from entry: {entry!r}"
            assert isinstance(entry["text_length"], int)
            assert entry["text_length"] > 0, f"text_length must be a positive int (entry existed): {entry!r}"

        # Defence in depth: the sensitive strings must not appear anywhere
        # in the entire crash_recovery.json blob — even in a key name,
        # comment, or nested object.
        assert "hunter2" not in crash_recovery_json, "secret value 'hunter2' found in crash_recovery.json"
        assert "John Doe" not in crash_recovery_json, "PII 'John Doe' found in crash_recovery.json"
        assert "patient" not in crash_recovery_json.lower(), "transcription content leaked into crash_recovery.json"

    def test_bundle_omits_text_even_when_pasted(self, recovery_dir):
        """The redaction applies regardless of the ``pasted`` flag.

        Even pasted entries (which the user did successfully deliver to
        the target app) must not leak their text in the diagnostic
        bundle — the bundle is shared with third parties (bug reports),
        so the pasted flag doesn't change the privacy posture.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("pasted secret: ssn 123-45-6789", pasted=True)
        cr.add("unpasted secret: credit card 4111 1111 1111 1111", pasted=False)

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            crash_recovery_json = zf.read("crash_recovery.json").decode("utf-8")

        # No text, no SSN, no CC number — even though the pasted flag is
        # preserved in metadata.
        assert "123-45-6789" not in crash_recovery_json
        assert "4111 1111 1111 1111" not in crash_recovery_json
        assert "4111111111111111" not in crash_recovery_json

        data = json.loads(crash_recovery_json)
        # The pasted flag itself should still be present so support can
        # see the paste success rate.
        pasted_flags = [e.get("pasted") for e in data["entries"]]
        assert pasted_flags == [True, False], f"pasted flags not preserved in metadata: {pasted_flags!r}"

    def test_bundle_metadata_includes_count_and_text_length(self, recovery_dir):
        """The redacted metadata envelope must include ``count`` and
        per-entry ``text_length`` so support engineers can still see how
        many entries existed and how long each was.

        Without ``text_length``, the bundle would be useless for
        diagnosing "the user said they dictated a paragraph but the
        recovery buffer was empty" type bugs.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("short")
        cr.add("a much longer transcription than the first one")

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("crash_recovery.json"))

        assert data["count"] == 2
        lengths = [e["text_length"] for e in data["entries"]]
        # The first entry is "short" (5 chars), the second is the longer
        # string (46 chars including spaces).
        assert lengths == [5, 46], f"unexpected text_lengths: {lengths!r}"
        # The second entry is genuinely longer than the first — sanity
        # check that text_length isn't always 0 or some constant.
        assert lengths[1] > lengths[0]

    def test_bundle_has_no_text_key_anywhere_in_zip(self, recovery_dir):
        """Defence in depth: the literal ``"text":`` JSON key must not
        appear anywhere in the bundle's ``crash_recovery.json``.

        This catches a future regression where someone adds a new field
        like ``text_preview`` that re-introduces the leak under a
        different key.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("the quick brown fox jumps over the lazy dog")

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            crash_recovery_json = zf.read("crash_recovery.json").decode("utf-8")

        # The JSON key ``"text"`` (with quotes) must not appear.
        assert '"text"' not in crash_recovery_json, (
            f"'text' JSON key found in crash_recovery.json — PII leak risk: {crash_recovery_json!r}"
        )
        # The transcribed phrase must not appear anywhere in the bundle
        # entry (would only happen if text was re-introduced).
        assert "quick brown fox" not in crash_recovery_json

    def test_bundle_redacts_all_secret_config_fields(self, recovery_dir):
        """PVT-G5-093: all 5 ``_SECRET_CONFIG_FIELDS`` must be redacted
        in the bundled ``config.json``.

        NF-R18-1 switched the IPC path from a hardcoded tuple to the
        canonical ``_SECRET_CONFIG_FIELDS`` frozenset in
        ``voice_typer/server/ipc_server.py``. The pre-fix hardcoded
        tuple missed ``cloud_api_key`` and ``groq_api_key``, leaking
        them in cleartext into bug-report attachments.

        This test pins the redaction so a future refactor that
        re-introduces a hardcoded tuple (or accidentally removes a
        field from ``_SECRET_CONFIG_FIELDS``) will fail loudly.
        """
        import json as _json

        from voice_typer.server.crash_recovery import CrashRecovery

        # Write a config.json with all 5 secret fields set to a unique,
        # easily-greppable sentinel value. If ANY of these survives into
        # the bundled config.json, the test fails.
        secret_sentinel = "PVT-G5-093-SECRET-DO-NOT-LEAK-7c8f3a2b"
        secret_fields = {
            "cloud_api_key": secret_sentinel,
            "openai_api_key": secret_sentinel,
            "groq_api_key": secret_sentinel,
            "deepgram_api_key": secret_sentinel,
            "llm_api_key": secret_sentinel,
        }
        # Include a few non-secret fields too, to verify they survive
        # the redaction pass (so we don't accidentally redact everything).
        non_secret_fields = {
            "model_size": "small",
            "device": "cpu",
            "paste_on_stop": True,
        }
        config_payload = {**secret_fields, **non_secret_fields}
        (recovery_dir / "config.json").write_text(_json.dumps(config_payload), encoding="utf-8")

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None, "create_diagnostic_bundle must return a path"

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            assert "config.json" in names, f"diagnostic bundle must include a config.json entry; got: {names!r}"
            bundled_config_raw = zf.read("config.json").decode("utf-8")

        # 1. The secret sentinel MUST NOT appear anywhere in the bundled
        #    config.json — defence in depth (covers key, value, nested
        #    object, JSON-escaped form, etc.).
        assert secret_sentinel not in bundled_config_raw, (
            f"secret value leaked into bundled config.json — raw bundled config: {bundled_config_raw!r}"
        )

        # 2. Each secret field must be present (so the operator can see
        #    that a key WAS configured) but redacted to "[REDACTED]".
        bundled_config = _json.loads(bundled_config_raw)
        for field in secret_fields:
            assert field in bundled_config, (
                f"secret field {field!r} missing from bundled config.json — "
                f"the redaction pass should preserve the key (set to "
                f"[REDACTED]) so support can see a key was configured. "
                f"Bundled config: {bundled_config!r}"
            )
            assert bundled_config[field] == "[REDACTED]", (
                f"secret field {field!r} not redacted in bundled config.json — got value: {bundled_config[field]!r}"
            )

        # 3. Non-secret fields must survive unredacted (so the operator
        #    can still diagnose model_size / device / etc.).
        for field, expected in non_secret_fields.items():
            assert bundled_config.get(field) == expected, (
                f"non-secret field {field!r} was altered by the redaction pass — "
                f"expected {expected!r}, got {bundled_config.get(field)!r}. "
                f"Bundled config: {bundled_config!r}"
            )


# ─── G4-M-33: archived crash_diagnostics in diagnostic bundle ──────────


class TestDiagnosticBundleArchiveInclusion:
    """G4-M-33: the diagnostic bundle includes archived crash_diagnostics
    files so support engineers can see crash records in bug reports."""

    def test_bundle_includes_archived_crash_diagnostics(self, recovery_dir):
        """The bundle zip includes files from ``crash_diagnostics_archive/``."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # Pre-populate the archive with a crash_diagnostics file.
        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "crash_diagnostics.1234.txt").write_text(
            "STATUS_ACCESS_VIOLATION: test crash\r\n", encoding="utf-8"
        )

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            # The archived file must be included under a
            # ``crash_diagnostics_archive/`` prefix.
            archived = [n for n in names if n.startswith("crash_diagnostics_archive/")]
            assert len(archived) >= 1, f"G4-M-33: bundle must include archived crash_diagnostics; got names: {names}"
            # The content must be preserved.
            content = zf.read("crash_diagnostics_archive/crash_diagnostics.1234.txt").decode("utf-8")
            assert "STATUS_ACCESS_VIOLATION" in content

    def test_bundle_includes_archived_python_crash_marker(self, recovery_dir):
        """G4-M-34: archived python_crash marker files are also included."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "python_crash.5678.txt").write_text(
            "exc_type=RuntimeError\nexc_value=test\nthread=MainThread\n",
            encoding="utf-8",
        )

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            archived = [n for n in names if "python_crash" in n]
            assert len(archived) >= 1, f"G4-M-34: bundle must include archived python_crash marker; got names: {names}"

    def test_bundle_without_archive_still_works(self, recovery_dir):
        """If no archive directory exists, the bundle is still created."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # No crash_diagnostics_archive/ directory.
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            # No crash_diagnostics_archive/ entries.
            assert not any(n.startswith("crash_diagnostics_archive/") for n in names)


# ─── G4-M-35: extended system_info ──────────────────────────────────────


class TestDiagnosticBundleExtendedSystemInfo:
    """G4-M-35: the system_info.txt in the bundle includes OS release,
    display server, audio devices, app version, and redacted env vars."""

    def test_system_info_includes_os_release(self, recovery_dir):
        import platform
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert f"OS release: {platform.release()}" in sys_info

    def test_system_info_includes_display_server(self, recovery_dir):
        import os
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        xdg = os.environ.get("XDG_SESSION_TYPE", "<unset>")
        wayland = os.environ.get("WAYLAND_DISPLAY", "<unset>")
        assert f"XDG_SESSION_TYPE: {xdg}" in sys_info
        assert f"WAYLAND_DISPLAY: {wayland}" in sys_info

    def test_system_info_includes_app_version(self, recovery_dir):
        import zipfile

        import voice_typer
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert f"App version: {voice_typer.__version__}" in sys_info

    def test_system_info_includes_tauri_sidecar_flag(self, recovery_dir):
        import os
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        tauri = os.environ.get("TAURI_SIDECAR", "<unset>")
        assert f"TAURI_SIDECAR: {tauri}" in sys_info

    def test_system_info_includes_voice_typer_env_vars(self, recovery_dir, monkeypatch):
        """VOICE_TYPER_* env vars are included (redacted/truncated)."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        monkeypatch.setenv("VOICE_TYPER_TEST_VAR", "test-value")
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert "env[VOICE_TYPER_TEST_VAR]=test-value" in sys_info

    def test_system_info_path_uses_basenames_only(self, recovery_dir, monkeypatch):
        """PATH is included as basenames only — no full path leakage."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # Set a PATH with a distinctive full path component.
        monkeypatch.setenv("PATH", "/home/secret_user/.local/bin:/usr/bin")
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        # The full path "/home/secret_user/.local/bin" must NOT appear.
        assert "/home/secret_user" not in sys_info, (
            "G4-M-35: PATH full path leaked into system_info; only basenames should appear"
        )
        # The basename "bin" should appear (from both /home/secret_user/.local/bin and /usr/bin).
        assert "bin" in sys_info
