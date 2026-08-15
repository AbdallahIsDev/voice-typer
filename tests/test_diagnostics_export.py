"""direct unit tests for
``voice_typer/server/diagnostics_export.py`` — the
``create_diagnostic_bundle`` function extracted from
``CrashRecovery.create_diagnostic_bundle`` (Phase 4.5 spaghetti
split).

Previously this module was tested only indirectly via
``CrashRecovery.create_diagnostic_bundle`` (the delegate — see
``tests/test_crash_recovery_diagnostic_bundle.py`` and
``tests/test_crash_recovery.py``). Those tests cover the integration
through the delegate; they do NOT pin the ``diagnostics_export``
module's own contracts (schema validation, PII redaction of all
secret fields, home-path redaction, partial-failure resilience, env-
var truncation, unicode preservation) against a future delegate-
removal refactor. These tests call
``diagnostics_export.create_diagnostic_bundle(recovery)`` directly so
the module's own invariants are pinned independently of the
``CrashRecovery`` delegate plumbing.

All heavy dependencies are mocked via the project-wide
``mock_heavy_imports`` autouse fixture (in ``tests/conftest.py``).
"""

from __future__ import annotations

import json
import zipfile

import pytest
from voice_typer.server import diagnostics_export

# ── Shared fixtures ───────────────────────────────────────────────────


@pytest.fixture
def recovery_dir(tmp_config_dir):
    """Point ``voice_typer.server.config._config_dir`` at a tmp_path.

    ``diagnostics_export.create_diagnostic_bundle`` looks up the
    config dir via ``_config_dir()`` (not the instance's
    ``self._path``) when deciding where to write the bundle zip — so
    the canonical ``tmp_config_dir`` fixture points the global at a
    temp dir. The same pattern is used in
    ``tests/test_crash_recovery_diagnostic_bundle.py`` and
    ``tests/test_crash_recovery.py``.
    """
    return tmp_config_dir


@pytest.fixture
def recovery(recovery_dir):
    """Build a real ``CrashRecovery`` instance backed by the tmp
    config dir.

    ``diagnostics_export.create_diagnostic_bundle(recovery)`` reads
    three attributes off the recovery instance:
      - ``recovery._path.parent``  (fallback config dir if
        ``_config_dir()`` raises — not exercised here because the
        fixture patches ``_config_dir`` to return ``recovery_dir``)
      - ``recovery._lock``         (per-instance mutex guarding
        ``_entries`` during the snapshot read)
      - ``recovery._entries``      (the recovery entries list — read
        under ``_lock`` to produce the metadata-only snapshot for
        ``crash_recovery.json``)
    A real ``CrashRecovery`` provides all three with the correct
    semantics; building a mock would require reproducing the lock +
    deque + entry-shape contract verbatim, which is brittle.
    """
    from voice_typer.server.crash_recovery import CrashRecovery

    return CrashRecovery(config_dir=recovery_dir)


# ── (a) bundle schema validation (required keys present) ──────────────


class TestBundleSchema:
    """The diagnostic bundle zip MUST contain the required sections
    so support engineers can rely on a stable schema:

      - ``system_info.txt``     (platform / Python / GPU / env)
      - ``crash_recovery.json`` (metadata-only entry snapshot — no
        transcription text per CR-39)
      - ``prewarm.json``        (prewarm probe — ``error`` key on failure)

    Optional sections (``voice-typer.log``, ``config.json``,
    ``model_info.txt``, ``crash_diagnostics_archive/*``) are gated on
    file existence / subsystem availability and are NOT required by
    the schema.

    (Wave 3, 2026-08-14): ``prewarm.json`` was REMOVED from the
    required-sections set — prewarm became a worker startup phase
    (master plan §6.2 P-1), so there is no longer a separate prewarm
    process / sentinel / PID file to probe. Support engineers
    investigating cache state should look at the worker's startup log
    instead. The previous ``test_prewarm_json_schema_on_success`` /
    ``test_prewarm_paths_have_home_prefix_replaced`` /
    ``test_prewarm_probe_failure_does_not_abort_bundle`` tests were
    deleted in lockstep.
    """

    def test_bundle_contains_required_sections(self, recovery) -> None:
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None, "create_diagnostic_bundle must return a path, not None"

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()

        # Required sections.
        # NOTE: ``prewarm.json`` was removed (prewarm became a worker
        # startup phase — master plan §6.2 P-1). The bundle no longer
        # emits a prewarm probe section.
        required = {"system_info.txt", "crash_recovery.json"}
        missing = required - set(names)
        assert not missing, f"diagnostic bundle missing required sections: {missing}. Got names: {names}"

    def test_crash_recovery_json_schema(self, recovery) -> None:
        """``crash_recovery.json`` MUST contain the ``entries`` and
        ``count`` keys so support engineers can see how many recovery
        entries existed without parsing the entries array."""
        recovery.add("first transcription")
        recovery.add("second transcription")

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("crash_recovery.json"))

        assert "entries" in data, f"crash_recovery.json missing 'entries' key; got: {data!r}"
        assert "count" in data, f"crash_recovery.json missing 'count' key; got: {data!r}"
        assert data["count"] == 2, (
            f"crash_recovery.json count must match the number of entries; expected 2, got {data['count']}"
        )
        assert isinstance(data["entries"], list)
        assert len(data["entries"]) == 2


# ── (b) PII redaction: secret config fields → masked ──────────────────


class TestConfigSecretRedaction:
    """All fields in ``_SECRET_CONFIG_FIELDS`` MUST be redacted to
    ``"[REDACTED]"`` in the bundled ``config.json``.

    Note on acceptance-criterion interpretation: the literal wording
    "api_key, oauth_token, password fields → masked" does not match
    the production code — ``diagnostics_export.create_diagnostic_bundle``
    redacts ONLY the fields in ``_SECRET_CONFIG_FIELDS`` (the 5 API-key
    fields derived from ``credential_store.PROVIDER_TO_CONFIG_FIELD``:
    ``openai_api_key``, ``groq_api_key``, ``deepgram_api_key``,
    ``cloud_api_key``, ``llm_api_key``). Arbitrary fields like
    ``password`` or ``oauth_token`` are NOT redacted by this code
    path — they may be redacted elsewhere (via ``redact_for_export``
    for logs) but NOT in config.json bundling. This test pins the
    actual behaviour: the 5 API-key fields are redacted; non-secret
    fields survive unredacted.

    Existing test ``test_bundle_redacts_all_secret_config_fields`` in
    ``tests/test_crash_recovery_diagnostic_bundle.py`` already covers
    the integration path through ``CrashRecovery.create_diagnostic_bundle``;
    this test calls ``diagnostics_export.create_diagnostic_bundle``
    directly to pin the module-level contract.
    """

    def test_all_secret_config_fields_redacted(self, recovery, recovery_dir) -> None:
        secret_sentinel = "TC10-SECRET-DO-NOT-LEAK-9f3a2b7c"
        secret_fields = {
            "openai_api_key": secret_sentinel,
            "groq_api_key": secret_sentinel,
            "deepgram_api_key": secret_sentinel,
            "cloud_api_key": secret_sentinel,
            "llm_api_key": secret_sentinel,
        }
        non_secret_fields = {
            "model_size": "small",
            "device": "cpu",
            "paste_on_stop": True,
        }
        config_payload = {**secret_fields, **non_secret_fields}
        (recovery_dir / "config.json").write_text(json.dumps(config_payload), encoding="utf-8")

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            bundled_config_raw = zf.read("config.json").decode("utf-8")

        # Defence in depth: the secret sentinel MUST NOT appear
        # anywhere in the bundled config.json.
        assert secret_sentinel not in bundled_config_raw, (
            f"secret value leaked into bundled config.json — raw: {bundled_config_raw!r}"
        )

        bundled_config = json.loads(bundled_config_raw)
        for field in secret_fields:
            assert field in bundled_config, (
                f"secret field {field!r} missing from bundled config.json — "
                f"the redaction pass should preserve the key (set to "
                f"[REDACTED]) so support can see a key was configured."
            )
            assert bundled_config[field] == "[REDACTED]", (
                f"secret field {field!r} not redacted — got value: {bundled_config[field]!r}"
            )
        # Non-secret fields must survive unredacted.
        for field, expected in non_secret_fields.items():
            assert bundled_config.get(field) == expected, (
                f"non-secret field {field!r} was altered by the redaction "
                f"pass — expected {expected!r}, got {bundled_config.get(field)!r}"
            )


# ── (c) home-path redaction ───────────────────────────────────────────


class TestHomePathRedaction:
    """UE-5-F2: filesystem paths embedded in the diagnostic bundle
    MUST be piped through ``_redact_home_path`` so the user-home prefix
    is replaced with ``~`` (raw paths like
    ``/home/alice/.voice-typer/voice-typer.log`` leak the OS username).

    The criterion's literal ``/home/user/`` → ``/home/<redacted>/``
    wording describes the spirit; the actual replacement is
    ``/home/user/...`` → ``~/...`` (``~`` is the conventional
    home-redaction token in this codebase).

    (Wave 3, 2026-08-14): the previous test pinned home-redaction on
    the prewarm ``sentinel_path`` / ``pid_file_path`` paths in
    ``prewarm.json``. Prewarm became a worker startup phase (master
    plan §6.2 P-1), so ``prewarm.json`` is no longer emitted and the
    previous test was deleted. The home-redaction contract itself is
    still exercised end-to-end by the bundle-path log redaction in
    ``create_diagnostic_bundle`` (the final ``log.info`` call pipes
    the bundle path through ``_redact_home_path``) — the new test
    below pins that invariant.
    """

    def test_bundle_path_is_home_redacted_in_log(self, recovery, recovery_dir, monkeypatch, caplog) -> None:
        """The bundle path logged at export-completion MUST be
        home-redacted so the OS username doesn't leak via the path.

        ``create_diagnostic_bundle`` calls ``_redact_home_path`` on
        the bundle path before logging it (the returned path string
        is NOT redacted — callers display it to the user who already
        knows their own home dir).
        """
        import logging as _logging

        from voice_typer.server._secrets import _redact_home_path

        # Patch expanduser so the home IS recovery_dir (the test's
        # tmp_path). create_diagnostic_bundle writes the bundle under
        # recovery_dir, so the bundle path resolves under recovery_dir;
        # _redact_home_path replaces the home prefix with ~.
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(recovery_dir) if p == "~" else p,
        )

        with caplog.at_level(_logging.INFO, logger="voice_typer.server.diagnostics_export"):
            bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        # The bundle path logged in the INFO message must NOT contain
        # the home-directory prefix (it was redacted to ~).
        expected_redacted = _redact_home_path(str(bundle_path))
        log_messages = [r.getMessage() for r in caplog.records]
        bundle_log_lines = [m for m in log_messages if "Diagnostic bundle created" in m]
        assert bundle_log_lines, (
            f"expected a 'Diagnostic bundle created' INFO log; got messages: {log_messages!r}"
        )
        assert str(recovery_dir) not in bundle_log_lines[0], (
            f"home prefix leaked into the bundle-path log line; got: {bundle_log_lines[0]!r}"
        )
        assert expected_redacted in bundle_log_lines[0], (
            f"bundle-path log line should contain the home-redacted form "
            f"{expected_redacted!r}; got: {bundle_log_lines[0]!r}"
        )


# ── (d) partial-failure: subsystem raises → bundle still produced ─────


class TestPartialFailure:
    """When one subsystem probe RAISES, the bundle MUST still be produced —
    the failing section is marked unavailable (the section's ``error``
    key is populated) rather than aborting the entire export.

    (Wave 3, 2026-08-14): the previous test pinned partial-failure
    resilience on the deleted ``voice_typer.server.prewarm.get_prewarm_status``
    probe. Prewarm became a worker startup phase (master plan §6.2
    P-1), so the prewarm probe was removed from ``diagnostics_export``.
    The partial-failure contract itself is still exercised by the
    sibling ``permissions.json`` probe (added in the same slice that
    deleted the prewarm probe) — that probe is wrapped in a
    ``try/except Exception`` that writes ``{"error": str(exc)}`` to
    ``permissions.json`` on failure rather than aborting the bundle.
    """

    def test_permissions_probe_failure_does_not_abort_bundle(self, recovery, monkeypatch) -> None:
        """A failure in the ``permissions.json`` probe MUST be captured
        as an ``error`` key in ``permissions.json`` and MUST NOT abort
        the rest of the bundle (``system_info.txt`` +
        ``crash_recovery.json`` must still be present).

        Mirrors the previous ``test_prewarm_probe_failure_does_not_abort_bundle``
        contract — partial-failure resilience is a module-level
        invariant that must hold for every probe wrapped in a
        ``try/except Exception`` block.
        """
        # Make the keyboard permission probe raise. The permissions
        # block calls ``check_keyboard_permission()`` first, so patching
        # that function to raise exercises the partial-failure path.
        def raising_keyboard():
            raise RuntimeError("keyboard probe blew up")

        monkeypatch.setattr(
            "voice_typer.server.permissions.check_keyboard_permission",
            raising_keyboard,
        )

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        # The bundle MUST still be produced (not None).
        assert bundle_path is not None, (
            "diagnostics_export must NOT return None when the permissions "
            "probe raises — the error should be captured in permissions.json"
        )

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            # permissions.json is emitted on both the success and
            # failure paths of the outer try/except — on failure, the
            # except branch writes a permissions.json with an error key.
            perms_data = json.loads(zf.read("permissions.json"))

        # Required sections must still be present (the failure did
        # not abort the rest of the bundle).
        assert "system_info.txt" in names, (
            f"system_info.txt missing after permissions probe failure; got names: {names}"
        )
        assert "crash_recovery.json" in names, (
            f"crash_recovery.json missing after permissions probe failure; got names: {names}"
        )
        # permissions.json must contain the error key.
        assert "error" in perms_data, (
            f"permissions.json must include 'error' when the probe raises; got: {perms_data!r}"
        )
        assert "keyboard probe blew up" in perms_data["error"], (
            f"permissions.json error must include the original exception message; got: {perms_data['error']!r}"
        )


# ── (e) bundle size cap: very large env-var value → truncated ─────────
#
# Note on acceptance-criterion interpretation: the literal wording
# "very large config → truncated" does not match the production code —
# ``config.json`` is bundled verbatim (no size cap on individual
# fields and no overall cap on the config.json blob). The only
# truncation in ``diagnostics_export`` is for ``VOICE_TYPER_*``
# env-var values in ``system_info.txt`` (truncated to 200 chars +
# "...(truncated)" marker). This test pins THAT truncation contract
# directly at the module level.


class TestEnvVarTruncation:
    """``VOICE_TYPER_*`` env-var values longer than 200 chars MUST be
    truncated (with a ``...(truncated)`` marker) in
    ``system_info.txt`` so a runaway env var doesn't blow up the
    bundle size or leak a multi-KB secret-shaped string.

    Order matters: redact first (catch secret-shaped prefixes), then
    truncate (so a truncated secret is never partially-shipped — a
    truncation in the middle of an ``sk-…`` run would defeat the
    pattern matcher).
    """

    def test_long_env_var_value_truncated_in_system_info(self, recovery, monkeypatch) -> None:
        # The production code redacts BEFORE truncating (so a truncated
        # secret is never partially-shipped). To exercise the
        # truncation path we need a value that:
        #   (a) survives redact_secret(aggressive=True) largely intact
        #       (so the redacted form is still > 200 chars), AND
        #   (b) is longer than 200 chars.
        # A run of short dot-separated tokens separated by spaces does
        # NOT match the bare-API-key pattern (the tokens are too short
        # and the spaces break up the alphanumeric runs), so the
        # redaction pass leaves them intact. Total > 200 chars
        # triggers the truncation marker.
        trailing = " ".join(f"a.b.{i:03d}" for i in range(60))  # ~720 chars
        long_value = "Bearer sk-test " + trailing
        monkeypatch.setenv("VOICE_TYPER_LONG_VAR", long_value)

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        # The env-var line must be present.
        assert "env[VOICE_TYPER_LONG_VAR]=" in sys_info, (
            f"VOICE_TYPER_LONG_VAR must be included in system_info.txt; got: {sys_info!r}"
        )
        # The sk-test secret MUST NOT appear (was redacted before
        # truncation — a truncation in the middle of an sk- run would
        # defeat the pattern matcher).
        assert "sk-test" not in sys_info, (
            "the 'sk-test' secret leaked into system_info.txt — it must be redacted BEFORE truncation"
        )
        # The full 700+ char trailing payload MUST NOT appear in full
        # (it was truncated to 200 chars + marker after redaction).
        assert trailing not in sys_info, (
            "the full trailing payload leaked into system_info.txt — it "
            "must be truncated to 200 chars + '...(truncated)' marker"
        )
        # The truncation marker MUST be present (the redacted value
        # was still > 200 chars after masking).
        assert "...(truncated)" in sys_info, (
            "system_info.txt must contain '...(truncated)' marker when an env-var value exceeds the 200-char cap"
        )

    def test_short_env_var_value_not_truncated(self, recovery, monkeypatch) -> None:
        """Sanity: short env-var values (under 200 chars) are NOT
        truncated — the cap only kicks in on oversized values."""
        # Use a value that survives redaction (short, with a space so
        # it doesn't match the bare-key pattern).
        short_value = "short value under cap"
        monkeypatch.setenv("VOICE_TYPER_SHORT_VAR", short_value)

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            sys_info = zf.read("system_info.txt").decode("utf-8")

        # The env-var line must be present with the value intact
        # (redaction passes short non-secret values through unchanged).
        assert f"env[VOICE_TYPER_SHORT_VAR]={short_value}" in sys_info, (
            f"short env-var value must be preserved unredacted and untruncated in system_info.txt; got: {sys_info!r}"
        )


# ── (f) unicode in config values → preserved ──────────────────────────


class TestUnicodePreservation:
    """Unicode characters in ``config.json`` values MUST be preserved
    through the redaction pass — support engineers sharing the bundle
    across locales need to see the actual config values (model names,
    user-set template strings, etc.) without mojibake.

    The production code uses ``json.dumps(data, indent=2)`` (default
    ``ensure_ascii=True``), which escapes non-ASCII to ``\\uXXXX``
    sequences. Parsing the JSON back yields the original unicode
    string — "preserved" means the round-trip is lossless, NOT that
    the raw bytes are UTF-8.
    """

    def test_unicode_in_config_value_round_trips(self, recovery, recovery_dir) -> None:
        unicode_payload = {
            "model_size": "small",
            "device": "cpu",
            # Unicode in a config value: a Chinese display name
            # that a user might set for a custom template / model alias.
            "custom_display_name": "你好世界 — café — 日本語",
            # Non-ASCII in a list value.
            "recent_phrases": ["hola", "你好", "こんにちは"],
        }
        (recovery_dir / "config.json").write_text(
            json.dumps(unicode_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            bundled_config_raw = zf.read("config.json").decode("utf-8")

        bundled_config = json.loads(bundled_config_raw)

        # The unicode string value MUST round-trip exactly.
        assert bundled_config.get("custom_display_name") == "你好世界 — café — 日本語", (
            f"unicode config value did not round-trip through the "
            f"redaction pass — expected '你好世界 — café — 日本語', "
            f"got {bundled_config.get('custom_display_name')!r}"
        )
        # Unicode in list values MUST round-trip too.
        assert bundled_config.get("recent_phrases") == ["hola", "你好", "こんにちは"], (
            f"unicode list values did not round-trip — got {bundled_config.get('recent_phrases')!r}"
        )
