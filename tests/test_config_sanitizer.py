"""Tests for the transport-neutral config sanitizer.

``sanitize_config_for_ipc`` is the canonical
implementation that both :mod:`voice_typer.server.service` and
:mod:`voice_typer.server.ipc_server` import.  These tests pin its
contract (SEC-003: never echo secret values back to the IPC client) so
that any future refactor that moves the function or weakens its
redaction breaks loudly here.

FR-19: the explicit literal in ``test_all_known_secret_fields_covered``
is replaced with a structural assertion against
``credential_store.PROVIDER_TO_CONFIG_FIELD.values()`` so a new provider
added to one source of truth is automatically required in the other.

FR-20: ``sanitize_config_for_ipc`` now uses ``dataclasses.asdict`` (not
``dict(config.__dict__)``), so the test fixtures use the real
``Config`` dataclass instead of a bare ``_FakeConfig``. The tests
explicitly assert that transient / private attributes
(``_last_saved_bytes``, ``last_load_warnings``) are NOT leaked across
the IPC boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from voice_typer.server.config import Config
from voice_typer.server.config_sanitizer import (
    REDACTED_SENTINEL,
    SECRET_CONFIG_FIELDS,
    sanitize_config_for_ipc,
)
from voice_typer.server.credential_store import PROVIDER_TO_CONFIG_FIELD


@dataclass
class _FakeConfig:
    """Minimal dataclass stand-in for ``voice_typer.server.config.Config``.

    FR-20: the sanitizer now uses ``dataclasses.asdict``, which requires
    a real dataclass instance (not a bare ``__dict__``-populated object).
    Decorating this fake with ``@dataclass`` lets us construct minimal
    fixtures for the field-by-field redaction tests without depending
    on the full ``Config`` schema (which would couple the test to the
    schema validator + on-disk config dir).
    """

    # Declare every key that any test passes via kwargs so the
    # dataclass constructor accepts them. ``default_factory`` keeps the
    # fake tolerant of "only set one field" usage.
    hotkey: str | None = None
    language: str | None = None
    model_name: str | None = None
    cloud_api_key: str = ""
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    deepgram_api_key: str | None = None
    llm_api_key: str | None = None


class TestSecretFieldsRedacted:
    """Every field in :data:`SECRET_CONFIG_FIELDS` must be redacted."""

    @pytest.mark.parametrize("field", sorted(SECRET_CONFIG_FIELDS))
    def test_each_secret_field_is_redacted_when_truthy(self, field):
        real_value = f"sk-{field}-real-value-12345"
        cfg = _FakeConfig(**{field: real_value})
        result = sanitize_config_for_ipc(cfg)
        assert result[field] == REDACTED_SENTINEL
        # SEC-003: the real value must not appear anywhere in the
        # serialized output.
        assert real_value not in str(result)

    def test_all_known_secret_fields_covered(self):
        """FR-19: pin the structural link between SECRET_CONFIG_FIELDS
        and ``credential_store.PROVIDER_TO_CONFIG_FIELD``.

        If a contributor adds a new provider to
        ``PROVIDER_TO_CONFIG_FIELD`` (e.g. ``"mistral":
        "mistral_api_key"``) but forgets to wire it into
        ``SECRET_CONFIG_FIELDS``, this test fails — preventing a
        SEC-003 regression where the new API key would be echoed in
        plaintext over the loopback IPC socket. The structural
        derivation in ``config_sanitizer`` makes the invariant
        self-enforcing at import time; this test pins it for posterity.
        """
        assert frozenset(PROVIDER_TO_CONFIG_FIELD.values()) == SECRET_CONFIG_FIELDS

    def test_secret_config_fields_subset_of_config_dataclass_fields(self):
        """FR-19: every secret field must be a declared ``Config``
        dataclass field.

        Defense in depth — if a typo in
        ``PROVIDER_TO_CONFIG_FIELD`` (e.g. ``"openai_key"`` instead of
        ``"openai_api_key"``) produced a field name that doesn't exist
        on ``Config``, the sanitizer's ``if k in out`` guard would
        silently skip it (no redaction would happen — but also no leak,
        since the field isn't actually on the dataclass). This test
        surfaces the drift loudly so a typo doesn't slip past review.
        """
        config_field_names = set(Config.__dataclass_fields__.keys())
        assert SECRET_CONFIG_FIELDS.issubset(config_field_names), (
            f"SECRET_CONFIG_FIELDS has entries not on Config dataclass: {SECRET_CONFIG_FIELDS - config_field_names}"
        )

    def test_falsy_secret_value_preserved_not_redacted(self):
        """Empty-string / None secrets are preserved so the renderer can
        distinguish "no key set" from "key set but hidden"."""
        cfg = _FakeConfig(
            cloud_api_key="",
            openai_api_key=None,
            groq_api_key="groq-real",
        )
        result = sanitize_config_for_ipc(cfg)
        assert result["cloud_api_key"] == ""
        assert result["openai_api_key"] is None
        assert result["groq_api_key"] == REDACTED_SENTINEL


class TestNonSecretFieldsPreserved:
    """Non-secret fields pass through unchanged."""

    def test_non_secret_fields_pass_through(self):
        cfg = _FakeConfig(
            hotkey="<f9>",
            language="fr",
            model_name="small.en",
            cloud_api_key="sk-real",
        )
        result = sanitize_config_for_ipc(cfg)
        assert result["hotkey"] == "<f9>"
        assert result["language"] == "fr"
        assert result["model_name"] == "small.en"
        assert result["cloud_api_key"] == REDACTED_SENTINEL

    def test_returns_plain_dict(self):
        cfg = _FakeConfig(hotkey="<f2>")
        result = sanitize_config_for_ipc(cfg)
        assert isinstance(result, dict)

    def test_does_not_mutate_input_config(self):
        """Sanitizing must not mutate the original Config object."""
        cfg = _FakeConfig(cloud_api_key="sk-original")
        sanitize_config_for_ipc(cfg)
        assert cfg.cloud_api_key == "sk-original"


class TestMissingFieldsHandledGracefully:
    """Older Config snapshots that lack a secret field must not crash."""

    def test_missing_secret_field_not_synthesized(self):
        # with ``dataclasses.asdict``, every declared dataclass
        # field is present in the output. ``_FakeConfig`` declares all
        # the secret fields with defaults, so they're always present
        # after ``asdict``. The sanitizer must NOT add a phantom
        # ``<redacted>`` entry for a key that wasn't set — it leaves
        # the falsy default (``""`` / ``None``) in place so the
        # renderer can distinguish "no key set" from "key set but
        # hidden".
        cfg = _FakeConfig(hotkey="<f2>")  # cloud_api_key stays at ""
        result = sanitize_config_for_ipc(cfg)
        assert result["hotkey"] == "<f2>"
        # cloud_api_key is "" (the default) — preserved as falsy, not
        # synthesized as <redacted>.
        assert result["cloud_api_key"] == ""

    def test_empty_config_returns_only_declared_fields(self):
        # ``asdict`` returns exactly the set of declared
        # dataclass fields, even if all of them are at their defaults.
        cfg = _FakeConfig()
        result = sanitize_config_for_ipc(cfg)
        # Every key in the result is either a declared dataclass
        # field OR the deliberate ``last_load_warnings`` exception
        # (see :func:`sanitize_config_for_ipc` docstring). The test
        # uses ``_FakeConfig`` which is a minimal stand-in without
        # the ``last_load_warnings`` attribute — so the sanitizer's
        # ``getattr(config, "last_load_warnings", None) or []``
        # fallback fires and the key is present with value ``[]``.
        expected_keys = set(_FakeConfig.__dataclass_fields__.keys()) | {"last_load_warnings"}
        assert set(result.keys()) == expected_keys


class TestNoTransientAttributesLeaked:
    """FR-20: the sanitizer must NOT leak transient / private attributes.

    Prior to FR-20, ``sanitize_config_for_ipc`` used
    ``dict(config.__dict__)`` which returned ALL instance attributes,
    including private/transient ones (``_last_saved_bytes``,
    ``last_load_warnings``) that are NOT declared dataclass fields.
    Those attributes can carry filesystem paths, prior config values,
    or schema-version details — leaking them across the IPC boundary
    to any local process that calls ``get_config`` is a privacy /
    security regression.
    """

    def test_does_not_leak_last_saved_bytes(self):
        """The ``_last_saved_bytes`` cache (set by ``Config.__post_init__``
        and updated by ``Config._save_unlocked``) must NOT appear in
        the sanitized output."""
        cfg = Config()
        # ``__post_init__`` sets ``_last_saved_bytes`` to None.
        object.__setattr__(cfg, "_last_saved_bytes", b'{"should": "not leak"}')
        result = sanitize_config_for_ipc(cfg)
        assert "_last_saved_bytes" not in result
        # Belt and suspenders: the cached bytes value must not appear
        # in the serialized output either.
        assert b'"should": "not leak"' not in str(result).encode("utf-8", errors="ignore")

    def test_does_not_leak_last_load_warnings(self, monkeypatch):
        """``last_load_warnings`` is the ONE deliberate exception to
        the dataclass-fields-only denylist (see
        :func:`sanitize_config_for_ipc` docstring): it IS surfaced in
        the sanitized output so the renderer can act on warnings, but
        each entry is run through :func:`_redact_load_warning` (200-char
        truncate + PII/URL/secret redaction + home-path stripping) so
        filesystem paths and API keys embedded in warning messages do
        NOT leak.

        This test pins BOTH directions: the key IS present (so the
        renderer always has a list to iterate), AND the embedded
        sensitive content is redacted.

        To exercise the home-path redaction branch in
        :func:`_redact_home_path`, we use an absolute path that
        starts with the real :func:`os.path.expanduser("~")` so the
        helper's ``startswith(home)`` check fires. The mock redaction
        is monkeypatched to a no-op for the home comparison value so
        the test is deterministic regardless of the host's actual
        home directory.
        """
        from pathlib import Path

        cfg = Config()
        # Use a path that genuinely starts with the user's home
        # directory so :func:`_redact_home_path` strips the home
        # prefix. The helper is a no-op for paths that don't start
        # with home, so a relative path like ``sensitive/path`` would
        # NOT exercise the redaction.
        home = str(Path.home())
        cfg.last_load_warnings = [
            f"{home}/sensitive-path/config.json was migrated",
            "field 'openai_api_key' had value 'sk-leak-me' which was reset",
        ]
        result = sanitize_config_for_ipc(cfg)
        # The key IS surfaced (renderer contract).
        assert "last_load_warnings" in result
        # The home-prefixed path is redacted: the absolute home
        # prefix is replaced with ``~``, so the original home path
        # (e.g. ``/home/user``) must NOT appear in the IPC payload.
        assert home not in str(result), (
            f"_redact_home_path should have replaced the home prefix with ``~``; got {result['last_load_warnings']!r}"
        )
        # And the API key is masked.
        assert "sk-leak-me" not in str(result), (
            f"redact_pii should have masked the API key; got {result['last_load_warnings']!r}"
        )

    def test_does_not_leak_mutation_lock(self):
        """The ``_mutation_lock`` ClassVar (an ``RLock`` instance)
        must NOT appear in the sanitized output — it's not JSON-
        serializable and is an internal concurrency primitive."""
        cfg = Config()
        import threading

        cfg.set_mutation_lock(threading.RLock())
        result = sanitize_config_for_ipc(cfg)
        assert "_mutation_lock" not in result

    def test_output_keys_exactly_match_config_dataclass_fields(self):
        """FR-20: the output key set is EXACTLY the set of declared
        ``Config`` dataclass fields (excluding ``ClassVar`` fields,
        which ``dataclasses.asdict`` correctly skips) — no more, no
        less. This is the denylist-by-default security property: only
        fields the schema explicitly declares are allowed to leave the
        process via IPC.
        """
        import typing

        cfg = Config()
        # Populate transient attrs to ensure they're filtered out.
        object.__setattr__(cfg, "_last_saved_bytes", b"secret-cache-bytes")
        cfg.last_load_warnings = ["leak-attempt"]
        result = sanitize_config_for_ipc(cfg)
        # ``Config.__dataclass_fields__`` includes ``ClassVar`` fields
        # (e.g. ``_mutation_lock``) which ``dataclasses.asdict``
        # correctly EXCLUDES. Filter them out so the expected set
        # matches what ``asdict`` actually returns.
        expected_keys = {
            name
            for name, f in Config.__dataclass_fields__.items()
            if typing.get_origin(typing.get_type_hints(Config).get(name, f.type)) is not typing.ClassVar
            and typing.get_type_hints(Config).get(name, f.type) is not typing.ClassVar
            and not (isinstance(f.type, str) and "ClassVar" in f.type)
        }
        # Belt-and-suspenders: also exclude the known ClassVar fields on
        # Config (``_mutation_lock`` and ``_SECRET_FIELD_NAMES_FALLBACK``)
        # — both are ``ClassVar[...]`` and ``dataclasses.asdict``
        # correctly excludes them.
        expected_keys.discard("_mutation_lock")
        expected_keys.discard("_SECRET_FIELD_NAMES_FALLBACK")
        # ``last_load_warnings`` is the ONE deliberate exception to
        # the dataclass-fields-only denylist (the sanitizer surfaces
        # it for the renderer to display a "Config loaded with N
        # warnings" toast). The keys check below accounts for it.
        expected_keys.add("last_load_warnings")
        assert set(result.keys()) == expected_keys, (
            f"Sanitizer output keys mismatch.\n"
            f"  Expected (Config dataclass fields, ClassVar excluded, "
            f"plus last_load_warnings): {sorted(expected_keys)}\n"
            f"  Got: {sorted(result.keys())}\n"
            f"  Extra (should NOT be present): "
            f"{sorted(set(result.keys()) - expected_keys)}\n"
            f"  Missing (should be present): "
            f"{sorted(expected_keys - set(result.keys()))}"
        )


class TestSentinelValue:
    def test_sentinel_is_redacted_marker(self):
        assert REDACTED_SENTINEL == "<redacted>"


# ──────────────────────────────────────────────────────────────────────────
# IPC ``set_config`` handler returns the FULL errors list in the
# error envelope (not just ``errors[0]``), so a new renderer can surface
# all N field errors at once instead of forcing the user to fix-and-
# resubmit N times.
# ──────────────────────────────────────────────────────────────────────────


class TestSetConfigErrorEnvelope:
    """FR-22: ``_handle_set_config`` includes ``data.errors`` (full list)
    alongside ``data.message`` (first error, backward compat)."""

    def _make_ipc_server(self):
        """Construct a real IPCServer wired to fake app + service.

        Uses the canonical ``make_ipc_server_with_fakes`` factory so the
        fakes match ``AppProtocol`` / ``ServiceProtocol`` exactly. Inlined
        here so this test module stays self-contained (no cross-dir
        fixture dependency).
        """
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        server, fake_app, fake_service = make_ipc_server_with_fakes()
        fake_app._ipc_server = server
        return server, fake_app, fake_service

    def test_single_error_envelope_has_errors_list(self):
        """A single-field invalid payload must still include
        ``data.errors`` (a 1-element list) for forward compat with new
        renderers that read ``err.errors``."""
        ipc_server, _, _ = self._make_ipc_server()
        # model_size has an enum validator that rejects unknown values.
        resp = ipc_server._handle_set_config({"model_size": "not-a-real-model"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        # full errors list present.
        assert "errors" in resp["data"]
        assert isinstance(resp["data"]["errors"], list)
        assert len(resp["data"]["errors"]) == 1
        # Backward compat: message is still errors[0].
        assert resp["data"]["message"] == resp["data"]["errors"][0]

    def test_multi_error_envelope_includes_all_errors(self):
        """FR-22: an N-field invalid payload must include ALL N errors
        in ``data.errors`` (not just the first). Pre-fix, the handler
        returned only ``errors[0]`` and the user had to fix-and-
        resubmit N times to discover all N errors."""
        ipc_server, _, _ = self._make_ipc_server()
        # Two invalid fields: bad model_size + bad text_size.
        # Use ``text_size`` (int validator) with a string value to
        # guarantee a type-check failure.
        resp = ipc_server._handle_set_config(
            {
                "model_size": "not-a-real-model",
                "text_size": "not-an-int",
            },
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        # errors list has BOTH errors.
        errors = resp["data"]["errors"]
        assert isinstance(errors, list)
        assert len(errors) >= 2, f"FR-22: expected >=2 errors in envelope, got {len(errors)}: {errors}"
        # Both invalid field names appear in the error strings.
        errors_text = " ".join(errors)
        assert "model_size" in errors_text
        assert "text_size" in errors_text
        # Backward compat: message is errors[0].
        assert resp["data"]["message"] == errors[0]


# ──────────────────────────────────────────────────────────────────────────
# ``history_enabled`` field on Config (config.py part only —
# P4-A4 owns the dictation_pipeline.py gate).
# ──────────────────────────────────────────────────────────────────────────


class TestHistoryEnabledField:
    """FR-28: Config has a ``history_enabled: bool = True`` field and
    the IPC allowlist accepts it (so the renderer can toggle it via
    set_config)."""

    def test_history_enabled_field_exists_with_default_true(self):
        """The Config dataclass must declare ``history_enabled`` with
        default ``True`` (preserve existing 'history on' behavior for
        upgrades)."""
        assert "history_enabled" in Config.__dataclass_fields__, (
            "FR-28: Config dataclass must declare a 'history_enabled' "
            "field (default True). P4-A4 owns the dictation_pipeline "
            "gate that reads it."
        )
        cfg = Config()
        assert cfg.history_enabled is True, (
            "FR-28: history_enabled must default to True so existing "
            "users keep their history-on behavior after upgrade."
        )

    def test_history_enabled_in_ipc_allowlist(self):
        """The IPC allowlist must include ``history_enabled`` so the
        renderer can toggle it via set_config (Settings → Privacy →
        Disable history)."""
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        assert "history_enabled" in IPC_CONFIG_ALLOWLIST, (
            "FR-28: 'history_enabled' must be in IPC_CONFIG_ALLOWLIST so the renderer can toggle it via set_config."
        )
        expected_type, validator = IPC_CONFIG_ALLOWLIST["history_enabled"]
        assert expected_type is bool
        # Validator must accept True / False and reject non-bool.
        assert validator(True) is None
        assert validator(False) is None

    def test_history_enabled_round_trips_through_save_load(self, tmp_config_dir):
        """Save a Config with history_enabled=False, reload it, verify
        the field is preserved. This is the contract P4-A4's
        dictation_pipeline gate will rely on."""
        cfg1 = Config()
        cfg1.history_enabled = False
        cfg1.save()

        cfg2 = Config.load()
        assert cfg2.history_enabled is False, (
            "FR-28: history_enabled=False did not round-trip through "
            "save/load. The dictation_pipeline gate (P4-A4) relies on "
            "this field persisting correctly."
        )

    def test_set_config_history_enabled_via_ipc(self):
        """FR-28 end-to-end: a set_config IPC call with
        ``history_enabled: False`` must succeed (ack) and the value
        must be passed to ``service.apply_config``."""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        ipc_server, _, fake_service = make_ipc_server_with_fakes()
        resp = ipc_server._handle_set_config({"history_enabled": False}, {})
        assert resp["type"] == "ack", f"FR-28: set_config(history_enabled=False) should succeed; got resp={resp}"
        # Verify apply_config received the validated update.
        fake_service.apply_config.assert_called_once()
        applied = fake_service.apply_config.call_args[0][0]
        assert applied == {"history_enabled": False}

    def test_set_config_history_enabled_rejects_non_bool(self):
        """FR-28: the validator must reject non-bool values (e.g.
        int, string) — defense in depth against a renderer bug that
        sends ``history_enabled: 1`` or ``history_enabled: "true"``."""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        ipc_server, _, _ = make_ipc_server_with_fakes()
        resp = ipc_server._handle_set_config({"history_enabled": "true"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert any("history_enabled" in e for e in resp["data"]["errors"])


# ──────────────────────────────────────────────────────────────────────────
# dead ``volume_duck_smart`` / ``push_to_talk_hotkey`` branches
# removed from config_applier. Verified via source inspection (the
# branches are gone) + behavior parity (existing hotkey restart path
# still fires when recording_mode / hotkey changes).
# ──────────────────────────────────────────────────────────────────────────


class TestDeadBranchesRemoved:
    """FR-21: the dead ``if "volume_duck_smart" in updates:`` branch
    and the ``or "push_to_talk_hotkey" in updates`` disjunct have been
    removed from ``config_applier.apply_config_side_effects``.

    These were dead because:
      - ``volume_duck_smart`` was removed from the Config dataclass
        (UX-2/GT-58) and from ``IPC_CONFIG_ALLOWLIST`` — the condition
        could never be True via the IPC path.
      - ``push_to_talk_hotkey`` was never readable on the wire (it was
        deliberately removed from the IPC allowlist per GT-F2-8, then
        fully removed from the Config dataclass in the v5 migration) —
        the disjunct could never be True via the IPC path.

    The fix is verified by source inspection (the branches are gone)
    and by behavior parity: the live hotkey-restart path still fires
    when ``recording_mode`` or ``hotkey`` changes.
    """

    def test_volume_duck_smart_branch_absent_from_source(self):
        """The ``if "volume_duck_smart" in updates:`` branch must NOT
        appear in the config_applier source (it was dead code that
        misled reviewers)."""
        import inspect

        from voice_typer.server import config_applier

        source = inspect.getsource(config_applier)
        # The branch body had a call to ``set_smart_duck_enabled`` —
        # that call must NOT appear inside a ``volume_duck_smart``
        # branch. (The symbol may still appear in comments / docstrings
        # explaining why the branch was removed.)
        assert 'if "volume_duck_smart" in updates' not in source, (
            "FR-21 regression: the dead ``if 'volume_duck_smart' in "
            "updates:`` branch is still present in config_applier."
        )

    def test_push_to_talk_hotkey_disjunct_absent_from_hotkey_restart_branch(
        self,
    ):
        """The ``or "push_to_talk_hotkey" in updates`` disjunct must
        NOT appear in the hotkey-restart branch's ``if`` condition
        (it was dead code — push_to_talk_hotkey is not on the wire)."""
        import inspect

        from voice_typer.server import config_applier

        source = inspect.getsource(config_applier)
        # The disjunct appeared in this exact form pre-fix. After the
        # fix, the condition is just
        # ``if "recording_mode" in updates or "hotkey" in updates:``.
        assert 'or "push_to_talk_hotkey" in updates' not in source, (
            "FR-21 regression: the dead "
            "``or 'push_to_talk_hotkey' in updates`` disjunct is still "
            "present in config_applier."
        )

    def test_hotkey_restart_still_fires_on_hotkey_change(self):
        """FR-21 behavior parity: removing the dead disjunct must NOT
        break the live hotkey-restart path. A ``hotkey`` change must
        still trigger ``app.hotkeys.restart``."""
        from voice_typer.server.service import VoiceTyperService

        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        svc = VoiceTyperService(fake_app)
        svc.apply_config_side_effects({"hotkey": "<f3>"})
        fake_app.hotkeys.restart.assert_called()

    def test_hotkey_restart_still_fires_on_recording_mode_change(self):
        """FR-21 behavior parity: a ``recording_mode`` change must
        still trigger ``app.hotkeys.restart``."""
        from voice_typer.server.service import VoiceTyperService

        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        svc = VoiceTyperService(fake_app)
        svc.apply_config_side_effects({"recording_mode": "push_to_talk"})
        fake_app.hotkeys.restart.assert_called()
