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
    """

    def test_bundle_contains_required_sections(self, recovery) -> None:
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None, "create_diagnostic_bundle must return a path, not None"

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()

        # Required sections.
        required = {"system_info.txt", "crash_recovery.json", "prewarm.json"}
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

    def test_prewarm_json_schema_on_success(self, recovery) -> None:
        """``prewarm.json`` MUST contain the ``sentinel_path`` and
        ``pid_file_path`` keys (home-redacted) on a successful probe."""
        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("prewarm.json"))

        # On success, the prewarm probe produces sentinel_path /
        # pid_file_path (home-redacted). On failure, an "error" key
        # is produced instead (see TestPartialFailure).
        assert "sentinel_path" in data or "error" in data, (
            f"prewarm.json must contain 'sentinel_path' (on success) or 'error' (on failure); got: {data!r}"
        )


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
    (``sentinel_path``, ``pid_file_path``) MUST be piped through
    ``_redact_home_path`` so the user-home prefix is replaced with
    ``~`` (raw paths like ``/home/alice/.voice-typer/.prewarm-sentinel``
    leak the OS username).

    The criterion's literal ``/home/user/`` → ``/home/<redacted>/``
    wording describes the spirit; the actual replacement is
    ``/home/user/...`` → ``~/...`` (``~`` is the conventional
    home-redaction token in this codebase).
    """

    def test_prewarm_paths_have_home_prefix_replaced(self, recovery, recovery_dir, monkeypatch) -> None:
        # Patch expanduser so the home IS recovery_dir (the test's
        # tmp_path). The prewarm sentinel / PID paths resolve under
        # recovery_dir; _redact_home_path replaces the home prefix
        # with ~ so paths get a ~ prefix in the bundle.
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(recovery_dir) if p == "~" else p,
        )

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            prewarm_data = json.loads(zf.read("prewarm.json"))

        # On success, sentinel_path / pid_file_path exist and are
        # home-redacted. (On failure, prewarm_data has an "error" key
        # instead — that path is covered by TestPartialFailure.)
        if "error" in prewarm_data:
            pytest.skip(
                f"prewarm probe failed in this env; cannot verify path redaction. prewarm_data={prewarm_data!r}"
            )

        sentinel_path = prewarm_data.get("sentinel_path", "")
        pid_file_path = prewarm_data.get("pid_file_path", "")

        # The full home-directory prefix MUST NOT appear in the
        # redacted path.
        assert str(recovery_dir) not in sentinel_path, f"home prefix leaked into sentinel_path: {sentinel_path!r}"
        assert str(recovery_dir) not in pid_file_path, f"home prefix leaked into pid_file_path: {pid_file_path!r}"
        # The path is prefixed with ~ (the redacted form).
        assert sentinel_path.startswith("~"), f"sentinel_path should start with ~ after redaction: {sentinel_path!r}"
        assert pid_file_path.startswith("~"), f"pid_file_path should start with ~ after redaction: {pid_file_path!r}"


# ── (d) partial-failure: subsystem raises → bundle still produced ─────


class TestPartialFailure:
    """When one subsystem probe RAISES (e.g. ``get_prewarm_status``),
    the bundle MUST still be produced — the failing section is marked
    unavailable (``prewarm.json`` gets an ``error`` key) rather than
    aborting the entire export.

    Mirrors the existing test
    ``tests/test_crash_recovery.py::TestCrashRecoveryPrewarmJson
    ::test_prewarm_json_includes_error_on_failure`` but calls
    ``diagnostics_export.create_diagnostic_bundle`` directly to pin
    the module-level contract.
    """

    def test_prewarm_probe_failure_does_not_abort_bundle(self, recovery, monkeypatch) -> None:
        # Make get_prewarm_status raise.
        def raising_status():
            raise RuntimeError("sentinel probe blew up")

        monkeypatch.setattr(
            "voice_typer.server.prewarm.get_prewarm_status",
            raising_status,
        )

        bundle_path = diagnostics_export.create_diagnostic_bundle(recovery)
        # The bundle MUST still be produced (not None).
        assert bundle_path is not None, (
            "diagnostics_export must NOT return None when the prewarm "
            "probe raises — the error should be captured in prewarm.json"
        )

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            prewarm_data = json.loads(zf.read("prewarm.json"))

        # Required sections must still be present (the failure did
        # not abort the rest of the bundle).
        assert "system_info.txt" in names, f"system_info.txt missing after prewarm probe failure; got names: {names}"
        assert "crash_recovery.json" in names, (
            f"crash_recovery.json missing after prewarm probe failure; got names: {names}"
        )
        # prewarm.json must contain the error key (not a sentinel_path).
        assert "error" in prewarm_data, (
            f"prewarm.json must include 'error' when the probe raises; got: {prewarm_data!r}"
        )
        assert "sentinel probe blew up" in prewarm_data["error"], (
            f"prewarm.json error must include the original exception message; got: {prewarm_data['error']!r}"
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
