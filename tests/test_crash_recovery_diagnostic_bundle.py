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
def recovery_dir(tmp_config_dir):
    """Point ``voice_typer.server.config._config_dir`` at a tmp_path.

    ``CrashRecovery.create_diagnostic_bundle`` looks up the config dir via
    ``_config_dir()`` (not the instance's ``self._path``) when deciding
    where to write the bundle zip — so we have to point the global at a
    temp dir. The canonical ``tmp_config_dir`` fixture does the patching;
    the same pattern is used in ``tests/test_crash_recovery.py``.
    """
    return tmp_config_dir


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


# archived crash_diagnostics in diagnostic bundle ──────────


class TestDiagnosticBundleArchiveInclusion:
    """G4-M-33: the diagnostic bundle includes archived crash_diagnostics
    files so support engineers can see crash records in bug reports."""

    def test_bundle_includes_archived_crash_diagnostics(self, recovery_dir):
        """The bundle zip includes files from ``crash_diagnostics_archive/``.

        UE-5-F1: archived files are now redacted line-by-line via
        ``redact_for_export`` (the unified PII + secret pipeline).
        The original test sentinel ``"STATUS_ACCESS_VIOLATION"`` (23
        chars, alphanumeric with underscores) triggered the generic
        20+ char alphanumeric-run pattern — a false-positive. The
        sentinel was changed to a short, non-secret-looking string
        so the file-inclusion assertion isn't masked by the (correct)
        aggressive redaction pass.
        """
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # Pre-populate the archive with a crash_diagnostics file.
        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "crash_diagnostics.1234.txt").write_text("test crash marker: short\n", encoding="utf-8")

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
            assert "test crash marker" in content

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


# extended system_info ──────────────────────────────────────


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


# archived crash dumps are REDACTED in the bundle ──────


class TestArchiveRedaction:
    """UE-5-F1: archived ``crash_diagnostics_archive/*`` files are
    redacted line-by-line via ``redact_for_export`` before being
    written into the diagnostic zip. Pre-fix, ``zf.write(...)`` shipped
    each archived file verbatim — leaking secrets embedded in prior
    session tracebacks (URL query-string ``?key=sk-…``, env-var dumps,
    bearer tokens, ``str(exception)`` payloads).
    """

    def test_archived_crash_dump_secret_is_redacted(self, recovery_dir):
        """An archived crash-dump file containing an ``sk-...`` API key
        in a traceback is redacted before being zipped."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "python_crash.1234.txt").write_text(
            f"Traceback (most recent call last):\n"
            f"  File 'app.py', line 42, in <module>\n"
            f"    raise RuntimeError('failed with key={secret}')\n"
            f"RuntimeError: failed with key={secret}\n",
            encoding="utf-8",
        )

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            content = zf.read("crash_diagnostics_archive/python_crash.1234.txt").decode("utf-8")

        # The secret MUST NOT appear anywhere in the archived entry.
        assert secret not in content, f"UE-5-F1 regression: secret leaked into archived crash dump:\n{content}"
        # The traceback structure (non-secret parts) survives.
        assert "Traceback" in content
        assert "RuntimeError" in content

    def test_archived_crash_dump_url_query_string_secret_redacted(self, recovery_dir):
        """UE-5-F1: a URL with ``?key=sk-...`` query-string secret in
        an archived crash dump is masked (defense in depth — the
        unified ``redact_for_export`` pipeline catches both userinfo
        AND query-string secrets via the F5 fix)."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "crash_diagnostics.5678.txt").write_text(
            f"GET https://api.example.com/?key={secret} → 500\n",
            encoding="utf-8",
        )

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            content = zf.read("crash_diagnostics_archive/crash_diagnostics.5678.txt").decode("utf-8")

        assert secret not in content
        # Host is preserved (the URL structure remains useful for
        # debugging).
        assert "api.example.com" in content

    def test_archived_crash_dump_pii_email_redacted(self, recovery_dir):
        """UE-5-F1: PII patterns (email, phone, SSN, CC) in archived
        crash dumps are masked by the unified pipeline."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "python_crash.9999.txt").write_text(
            "user contact: alice@example.com phone: +1 (415) 555-2671\n",
            encoding="utf-8",
        )

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            content = zf.read("crash_diagnostics_archive/python_crash.9999.txt").decode("utf-8")

        assert "alice@example.com" not in content
        assert "555-2671" not in content
        assert "[EMAIL]" in content
        assert "[PHONE]" in content

    def test_archived_crash_dump_redaction_failure_skips_file(self, recovery_dir, monkeypatch):
        """UE-5-F1: if ``redact_for_export`` raises on an archived
        file (e.g. archive file is unreadable), the file is SKIPPED
        (defense in depth) — never shipped raw. The skip is logged at
        WARNING (UE-5-F8)."""
        import zipfile

        from voice_typer.server import _secrets as secrets_mod
        from voice_typer.server.crash_recovery import CrashRecovery

        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "crash_diagnostics.fail.txt").write_text("harmless content\n", encoding="utf-8")

        # Force redact_for_export to raise so the skip-on-failure
        # branch fires. Patch the module-level attribute (the import
        # inside ``create_diagnostic_bundle`` looks it up at call
        # time, so the patch takes effect).
        call_count = {"n": 0}

        def _raise_on_archive_only(text: str) -> str:
            call_count["n"] += 1
            # The live-log redaction also uses redact_for_export;
            # only raise for inputs that look like archived crash
            # dump content ("harmless content").
            if "harmless content" in text:
                raise RuntimeError("redactor broken on archive path")
            return text

        monkeypatch.setattr(secrets_mod, "redact_for_export", _raise_on_archive_only)

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            # The archived file MUST NOT be in the zip (it was
            # skipped because redaction failed).
            assert "crash_diagnostics_archive/crash_diagnostics.fail.txt" not in names, (
                f"UE-5-F1 regression: archived file shipped raw despite redaction failure: {names!r}"
            )

    def test_archived_file_with_invalid_utf8_handled(self, recovery_dir):
        """UE-5-F1: archived files with invalid UTF-8 bytes are read
        with ``errors="replace"`` so the redactor doesn't crash on
        binary-ish crash dumps (e.g. Windows minidumps with embedded
        text)."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        archive_dir = recovery_dir / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True)
        # Write a file with invalid UTF-8 bytes mixed with valid text.
        (archive_dir / "crash_diagnostics.binary.txt").write_bytes(
            b"Traceback (most recent call last):\n  \xff\xfe invalid utf8 bytes here\nRuntimeError: boom\n"
        )

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            content = zf.read("crash_diagnostics_archive/crash_diagnostics.binary.txt").decode(
                "utf-8", errors="replace"
            )

        # The traceback structure (valid-UTF-8 parts) survives.
        assert "Traceback" in content
        assert "RuntimeError" in content


# VOICE_TYPER_* env vars are redacted ──────────────────


class TestEnvVarRedaction:
    """UE-5-F3: VOICE_TYPER_* env-var values are piped through
    ``redact_secret(value, aggressive=True)`` before being written
    into the bundle. Pre-fix, a user-set
    ``VOICE_TYPER_API_KEY=sk-...`` would ship in the bundle verbatim.
    """

    def test_voice_typer_env_var_with_api_key_redacted(self, recovery_dir, monkeypatch):
        """An ``sk-...`` API key in a VOICE_TYPER_* env var is masked."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        monkeypatch.setenv("VOICE_TYPER_API_KEY", secret)
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert secret not in sys_info, (
            f"UE-5-F3 regression: secret-bearing env var leaked into system_info:\n{sys_info}"
        )
        # The key name is preserved (so support can see a key WAS
        # configured); only the value is masked.
        assert "env[VOICE_TYPER_API_KEY]" in sys_info

    def test_voice_typer_env_var_with_bearer_token_redacted(self, recovery_dir, monkeypatch):
        """A Bearer token in a VOICE_TYPER_* env var is masked."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        monkeypatch.setenv(
            "VOICE_TYPER_AUTH_HEADER",
            "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890",
        )
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in sys_info
        assert "env[VOICE_TYPER_AUTH_HEADER]" in sys_info

    def test_benign_voice_typer_env_var_passes_through(self, recovery_dir, monkeypatch):
        """A benign VOICE_TYPER_* value (no secret pattern) passes
        through unchanged (false-positive guard)."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", "/opt/voice-typer/native")
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert "env[VOICE_TYPER_NATIVE_DIR]=/opt/voice-typer/native" in sys_info

    def test_long_voice_typer_env_var_truncated_after_redaction(self, recovery_dir, monkeypatch):
        """Very long values are truncated AFTER redaction so a
        truncated secret is never partially-shipped.

        We construct a value that contains a Bearer token followed by
        300+ non-secret chars. ``redact_secret`` masks the Bearer
        token (preserving the ``Bearer `` prefix) and leaves the
        trailing chars intact — total > 200 chars — so the truncation
        marker IS appended.
        """
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # Bearer token (masked, prefix preserved → "Bearer ***") +
        # 300 trailing chars. The trailing chars are short tokens
        # separated by ``.`` (NOT a long bare token) so the
        # bare-token redaction pattern doesn't match them; the only
        # redaction is the Bearer + sk- masking at the start, which
        # leaves a small prefix and a long unbroken suffix — total
        # > 200 chars triggers the truncation marker.
        trailing = " ".join(f"a.b.{i:03d}" for i in range(60))  # ~12*60 = 720 chars
        long_value = "Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890 " + trailing
        monkeypatch.setenv("VOICE_TYPER_LONG", long_value)
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        # The sk-… secret MUST NOT appear (was redacted before
        # truncation — a truncation in the middle of an ``sk-…`` run
        # would defeat the pattern matcher).
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in sys_info
        # The truncation marker IS present (the redacted value was
        # still > 200 chars after masking: "Bearer *** " + 300 chars
        # = 312 chars).
        assert "...(truncated)" in sys_info, (
            f"UE-5-F3: expected truncation marker for long env var value; sys_info:\n{sys_info}"
        )


# home-directory prefix redacted in prewarm.json + log ─


class TestHomePathRedaction:
    """UE-5-F2: filesystem paths embedded in the diagnostic bundle
    (``sentinel_path``, ``pid_file_path``, ``bundle_path``) are piped
    through ``_redact_home_path`` so the user-home prefix is replaced
    with ``~`` (the raw paths like
    ``/home/alice/.voice-typer/.prewarm-sentinel`` leak the OS
    username).
    """

    def test_prewarm_json_sentinel_path_redacted(self, recovery_dir, monkeypatch):
        """The ``sentinel_path`` field in ``prewarm.json`` is
        home-redacted."""
        import json as _json
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # Patch expanduser so the home IS recovery_dir (the test's
        # tmp_path). The bundle's prewarm.json contains paths under
        # the recovery_dir; ``_redact_home_path`` replaces the
        # home prefix with ``~`` so paths get a ``~`` prefix in the
        # bundle.
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(recovery_dir) if p == "~" else p,
        )
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            prewarm_data = _json.loads(zf.read("prewarm.json").decode("utf-8"))

        sentinel_path = prewarm_data.get("sentinel_path", "")
        # The full home-directory prefix MUST NOT appear in the
        # redacted path.
        assert str(recovery_dir) not in sentinel_path, (
            f"UE-5-F2 regression: home prefix leaked into sentinel_path: {sentinel_path!r}"
        )
        # The path is prefixed with ``~`` (the redacted form).
        assert sentinel_path.startswith("~"), (
            f"UE-5-F2: sentinel_path should start with ~ after redaction: {sentinel_path!r}"
        )

    def test_prewarm_json_pid_file_path_redacted(self, recovery_dir, monkeypatch):
        """The ``pid_file_path`` field in ``prewarm.json`` is
        home-redacted."""
        import json as _json
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # See ``test_prewarm_json_sentinel_path_redacted`` for the
        # rationale on pinning expanduser to recovery_dir.
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(recovery_dir) if p == "~" else p,
        )
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            prewarm_data = _json.loads(zf.read("prewarm.json").decode("utf-8"))

        pid_file_path = prewarm_data.get("pid_file_path", "")
        assert str(recovery_dir) not in pid_file_path
        assert pid_file_path.startswith("~")

    def test_bundle_path_in_log_is_redacted(self, recovery_dir, monkeypatch, caplog):
        """UE-5-F2: the ``[RECOVERY] Diagnostic bundle created: <path>``
        log message uses the home-redacted path (the returned path
        string is NOT redacted — callers like the IPC handler display
        it to the user who knows their own home dir)."""
        import logging

        from voice_typer.server.crash_recovery import CrashRecovery

        # ``_redact_home_path`` only redacts when the path starts
        # with the home dir (resolved via ``os.path.expanduser``).
        # The default ``recovery_dir`` is under /tmp; the test's
        # intent is to verify that paths under the home dir get
        # redacted, so we patch expanduser to make the home BE the
        # recovery_dir's parent — any path under recovery_dir will
        # then start with the (mocked) home and be redacted to a
        # ``~`` prefix.
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(recovery_dir) if p == "~" else p,
        )
        cr = CrashRecovery(config_dir=recovery_dir)
        with caplog.at_level(logging.INFO, logger="voice_typer.server.diagnostics_export"):
            returned_path = cr.create_diagnostic_bundle()

        # The returned path is the ACTUAL filesystem path (not
        # redacted) — callers display it to the user.
        assert returned_path is not None
        assert "/home/test_user" in returned_path or str(recovery_dir) in returned_path

        # The LOG message uses the redacted path.
        info_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        diagnostic_msgs = [m for m in info_messages if "Diagnostic bundle created" in m]
        assert diagnostic_msgs, f"expected 'Diagnostic bundle created' INFO log; got: {info_messages}"
        msg = diagnostic_msgs[-1]
        # The username MUST NOT leak via the log message.
        assert "/home/test_user" not in msg, f"UE-5-F2 regression: home prefix leaked into log message: {msg!r}"
        # The ``~`` prefix IS present (the redacted form).
        assert "~" in msg, f"UE-5-F2: log message should contain ~: {msg!r}"


# bundle uses mkstemp (not fixed-name .zip.tmp) ────────


class TestMkstemp:
    """UE-5-F6: the diagnostic bundle tmp file is created via
    ``tempfile.mkstemp`` (NOT the pre-fix fixed name
    ``bundle_path.with_suffix(".zip.tmp")``). The fixed name collided
    on ``O_EXCL`` if two exports ran concurrently."""

    def test_bundle_uses_mkstemp_for_tmp_file(self, recovery_dir, monkeypatch):
        """Source-level: ``create_diagnostic_bundle`` calls
        ``tempfile.mkstemp`` (not ``bundle_path.with_suffix``)."""
        import inspect

        from voice_typer.server.diagnostics_export import create_diagnostic_bundle

        src = inspect.getsource(create_diagnostic_bundle)
        assert "tempfile.mkstemp" in src, "UE-5-F6 regression: bundle no longer uses tempfile.mkstemp"
        # The actual assignment line using ``with_suffix`` for the tmp
        # path must NOT be present (a comment referencing the old
        # form is fine).
        assert "tmp_bundle_path = bundle_path.with_suffix" not in src, (
            "UE-5-F6 regression: bundle still uses fixed .zip.tmp name"
        )

    def test_concurrent_exports_do_not_clobber_each_other(self, recovery_dir):
        """UE-5-F6: two consecutive ``create_diagnostic_bundle`` calls
        (simulating concurrent exports) both succeed — the second
        call doesn't clobber the first's tmp file.

        Pre-fix, the fixed ``.zip.tmp`` name meant the second call's
        ``zipfile.ZipFile(..., "w", ...)`` would overwrite the first
        call's tmp file mid-write if the calls overlapped (rare in
        practice, but the race existed).
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        first = cr.create_diagnostic_bundle()
        second = cr.create_diagnostic_bundle()
        # Both calls must succeed (return a path, not None).
        assert first is not None
        assert second is not None
        # The two bundles are distinct files (different timestamps).
        assert first != second

    def test_no_zip_tmp_file_left_in_config_dir_after_success(self, recovery_dir):
        """UE-5-F6: after a successful export, no ``.tmp`` file is
        left in the config dir (the tmp file was renamed to the
        final path via ``os.replace``)."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.create_diagnostic_bundle()

        # No .tmp files should remain.
        tmp_files = list(recovery_dir.glob("*.tmp"))
        assert tmp_files == [], f"UE-5-F6: tmp files left in config dir after successful export: {tmp_files!r}"


# audio device names are PII-redacted ──────────────────


class TestDeviceNameRedaction:
    """UE-5-F9: audio device names are redacted via ``redact_pii``
    before being written into ``system_info.txt``. Device names like
    "John's AirPods Pro" can carry user-identifying names; Bluetooth
    device names in particular are user-settable."""

    def test_device_name_with_email_redacted(self, recovery_dir, monkeypatch):
        """A device name containing an email-shaped string is masked."""
        import zipfile

        # Stub sounddevice.query_devices to return a device with an
        # email-shaped name.
        fake_devices = [
            {"hostapi": 0, "name": "alice@example.com mic", "max_input_channels": 1, "default_samplerate": 48000},
        ]

        class _StubSounddevice:
            @staticmethod
            def query_devices():
                return fake_devices

        import sys

        monkeypatch.setitem(sys.modules, "sounddevice", _StubSounddevice)

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert "alice@example.com" not in sys_info, f"UE-5-F9 regression: email in device name leaked:\n{sys_info}"
        assert "[EMAIL]" in sys_info

    def test_device_name_with_phone_redacted(self, recovery_dir, monkeypatch):
        """A device name containing a phone number is masked."""
        import zipfile

        fake_devices = [
            {
                "hostapi": 0,
                "name": "call +1 (415) 555-2671 headset",
                "max_input_channels": 1,
                "default_samplerate": 48000,
            },
        ]

        class _StubSounddevice:
            @staticmethod
            def query_devices():
                return fake_devices

        import sys

        monkeypatch.setitem(sys.modules, "sounddevice", _StubSounddevice)

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        assert "555-2671" not in sys_info
        assert "[PHONE]" in sys_info

    def test_benign_device_name_preserved(self, recovery_dir, monkeypatch):
        """A benign device name (no PII patterns) is preserved
        unchanged (false-positive guard)."""
        import zipfile

        fake_devices = [
            {"hostapi": 0, "name": "External Microphone Array", "max_input_channels": 2, "default_samplerate": 48000},
        ]

        class _StubSounddevice:
            @staticmethod
            def query_devices():
                return fake_devices

        import sys

        monkeypatch.setitem(sys.modules, "sounddevice", _StubSounddevice)

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        # The benign name survives (redact_pii doesn't false-positive
        # on a 25-char run without PII patterns).
        assert "External Microphone Array" in sys_info
