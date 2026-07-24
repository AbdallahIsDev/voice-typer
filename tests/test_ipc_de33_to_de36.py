"""DE-33 / DE-34 / DE-35 / DE-36 regression tests for the ``ipc/`` package.

Each test class covers ONE finding from the comprehensive review
(``comprehensive-review.md`` lines 695–775). The file is self-contained
— it does NOT depend on the conftest fixtures from ``tests/handlers/``
so it can run in isolation and so the contract tests can import the
canonical ``_COMMAND_REGISTRY`` without triggering the handler-mixin
import cycle.

Findings covered:

* **DE-33** — ``ipc/history_bounds._sanitize_config_for_ipc``: pattern-
  based secret-field denylist + non-None-value redaction. The static
  ``_SECRET_CONFIG_FIELDS`` frozenset is retained for backward compat
  with ``crash_recovery.py``, but the sanitizer now ALSO consults
  :data:`_SECRET_FIELD_PATTERNS` so a future secret field (e.g.
  ``azure_api_key``, ``oauth_token``, ``client_secret``) is masked
  even if no one remembers to add it to the frozenset.
* **DE-34** — ``ipc/rate_limiter.COMMAND_COSTS``: the map now lists
  every command in the dispatcher's ``_COMMAND_REGISTRY`` so the
  contract test can fail-loud if a future command is registered
  without a cost entry.
* **DE-35** — ``ipc/history_bounds._bound_history_offset``: offset is
  now capped at :data:`_HISTORY_OFFSET_MAX` (10_000_000) in addition
  to the ``max(0, v)`` floor. Previously Python big-ints could pass
  the clamp and reach SQLite.
* **DE-36** — ``ipc/validation._validate_dict_payload``: migrated to
  emit namespaced codes (``client.invalid_payload`` /
  ``client.invalid_field`` / ``client.missing_field``) as the primary
  ``code`` field; the legacy bare form is preserved in a sibling
  ``legacy_code`` field for one release cycle.
"""

from __future__ import annotations

import dataclasses

import pytest

# ── DE-33 / DE-35 imports ────────────────────────────────────────────────
from voice_typer.server.ipc.history_bounds import (
    _HISTORY_OFFSET_MAX,
    _REDACTED_SENTINEL,
    _SECRET_CONFIG_FIELDS,
    _SECRET_FIELD_PATTERNS,
    _bound_history_limit,
    _bound_history_offset,
    _is_secret_field_name,
    _sanitize_config_for_ipc,
)

# ── DE-34 imports ────────────────────────────────────────────────────────
from voice_typer.server.ipc.rate_limiter import (
    COMMAND_COSTS,
    DEFAULT_COST,
    _RateLimiter,
)

# ── DE-36 imports ────────────────────────────────────────────────────────
from voice_typer.server.ipc.validation import (
    ERROR_CODES,
    _validate_dict_payload,
)

# ── DE-34 contract-test import: the canonical command registry ──────────
# ``_COMMAND_REGISTRY`` is a CLASS attribute on ``IPCServer`` (a dict
# mapping command name → handler method name). Importing the class
# triggers the handler-mixin imports; that's fine in a test process
# (the mixins are designed to be imported at test-collection time).
from voice_typer.server.ipc_server import IPCServer


# ══════════════════════════════════════════════════════════════════════════
# DE-33: pattern-based secret-field denylist + non-None redaction
# ══════════════════════════════════════════════════════════════════════════


class _ConfigLike:
    """Minimal stand-in for the real ``Config`` dataclass.

    The real ``Config`` is a dataclass with ~80 fields; constructing
    one in a unit test triggers credential-store integration and
    platform-default probes. This stand-in lets the sanitizer tests
    inject arbitrary fields via ``__dict__`` without touching the real
    class.
    """

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestIsSecretFieldName:
    """DE-33: ``_is_secret_field_name`` matches the documented patterns."""

    @pytest.mark.parametrize(
        "name",
        [
            # Explicit allowlist (backward compat with crash_recovery.py).
            "cloud_api_key",
            "openai_api_key",
            "groq_api_key",
            "deepgram_api_key",
            "llm_api_key",
            # Suffix-pattern denylist (defense-in-depth).
            "azure_api_key",
            "anthropic_api_key",
            "whisper_api_key",
            "access_token",
            "refresh_token",
            "oauth_token",
            "bearer_token",
            "client_secret",
            "signing_secret",
            "user_password",
            "admin_password",
            "db_password",
            "aws_credential",
            "service_credential",
            "auth_bearer",
        ],
    )
    def test_matches_secret_patterns(self, name):
        assert _is_secret_field_name(name) is True, (
            f"Field {name!r} should be classified as secret by either "
            f"_SECRET_CONFIG_FIELDS or _SECRET_FIELD_PATTERNS."
        )

    @pytest.mark.parametrize(
        "name",
        [
            # Plain config fields.
            "hotkey",
            "language",
            "model_size",
            "cloud_api_url",  # URL, not a key
            "llm_api_url",
            "llm_model",
            "vocabulary_enabled",
            # Boolean flag with "password" substring — does NOT match
            # because it ends in "_paste", not "_password".
            "warn_password_paste",
            # Field with "credential" substring but not as suffix.
            "credential_store_enabled",
            # Field with "bearer" substring but not as suffix.
            "bearer_mode",
        ],
    )
    def test_does_not_match_benign_fields(self, name):
        assert _is_secret_field_name(name) is False, (
            f"Field {name!r} should NOT be classified as secret — the "
            f"pattern is name-based (suffix or exact match), so "
            f"substring matches like 'warn_password_paste' are NOT "
            f"redacted (it ends in '_paste', not '_password')."
        )

    def test_exact_match_password(self):
        assert _is_secret_field_name("password") is True

    def test_exact_match_credential(self):
        assert _is_secret_field_name("credential") is True

    def test_exact_match_bearer(self):
        assert _is_secret_field_name("bearer") is True

    def test_exact_match_secret(self):
        assert _is_secret_field_name("secret") is True

    def test_exact_match_token(self):
        assert _is_secret_field_name("token") is True

    def test_exact_match_api_key(self):
        assert _is_secret_field_name("api_key") is True


class TestSanitizePatternDenylist:
    """DE-33: unlisted secret fields are redacted via the pattern denylist."""

    @pytest.mark.parametrize(
        "field_name, value",
        [
            ("azure_api_key", "sk-azure-12345"),
            ("anthropic_api_key", "sk-ant-67890"),
            ("oauth_token", "oauth-token-abcdef"),
            ("refresh_token", "refresh-token-xyz"),
            ("client_secret", "client-secret-value"),
            ("signing_secret", "signing-secret-value"),
            ("user_password", "p@ssw0rd"),
            ("db_password", "db-p@ssw0rd"),
            ("aws_credential", "AKIAIOSFODNN7EXAMPLE"),
            ("auth_bearer", "Bearer abc123"),
        ],
    )
    def test_unlisted_secret_field_is_redacted(self, field_name, value):
        """A secret-bearing field NOT in ``_SECRET_CONFIG_FIELDS`` must
        still be redacted by the pattern-based denylist (DE-33 defense-
        in-depth). The renderer must never see the real value."""
        cfg = _ConfigLike(**{field_name: value})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL, (
            f"Pattern-denylist failed for {field_name!r}: expected "
            f"{_REDACTED_SENTINEL!r}, got {out[field_name]!r}. The field "
            f"matches a _SECRET_FIELD_PATTERNS entry and must be "
            f"redacted even though it's not in _SECRET_CONFIG_FIELDS."
        )

    def test_exact_name_password_is_redacted(self):
        """A field literally named ``password`` (exact-match pattern)
        is redacted."""
        cfg = _ConfigLike(password="hunter2")
        out = _sanitize_config_for_ipc(cfg)
        assert out["password"] == _REDACTED_SENTINEL

    def test_exact_name_credential_is_redacted(self):
        cfg = _ConfigLike(credential="aws-cred-blob")
        out = _sanitize_config_for_ipc(cfg)
        assert out["credential"] == _REDACTED_SENTINEL

    def test_exact_name_bearer_is_redacted(self):
        cfg = _ConfigLike(bearer="Bearer xyz")
        out = _sanitize_config_for_ipc(cfg)
        assert out["bearer"] == _REDACTED_SENTINEL

    def test_warn_password_paste_NOT_redacted(self):
        """The boolean flag ``warn_password_paste`` (a real Config
        field) must NOT be redacted — the pattern is name-based, and
        the field ends in ``_paste``, not ``_password``. The renderer
        needs the real boolean value to render the toggle UI."""
        cfg = _ConfigLike(warn_password_paste=True)
        out = _sanitize_config_for_ipc(cfg)
        assert out["warn_password_paste"] is True, (
            "warn_password_paste is a boolean flag (NOT a secret); "
            "it must not be redacted by the pattern-based denylist."
        )

    def test_cloud_api_url_NOT_redacted(self):
        """``cloud_api_url`` ends in ``_url``, not ``_api_key`` — must
        not be redacted. The renderer needs the URL to display it."""
        cfg = _ConfigLike(cloud_api_url="https://api.example.com/v1")
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_url"] == "https://api.example.com/v1"


class TestSanitizeFalsyValues:
    """DE-33: redaction now masks any non-None value, regardless of truthiness.

    Pre-DE-33 the redaction logic was ``out[k] = _REDACTED_SENTINEL if v else v``
    — a secret stored as ``0``, ``False``, or ``""`` would NOT be
    redacted (falsy values were preserved verbatim). This was fine for
    the empty-string "no key set" case but unsafe for ``0`` / ``False``
    secrets and inconsistent with the documented "key is set" semantic.
    """

    def test_falsy_zero_is_redacted(self):
        """A secret stored as ``0`` (integer) is redacted — previously
        the truthy-only check preserved it verbatim, leaking the value."""
        cfg = _ConfigLike(azure_api_key=0)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_falsy_false_is_redacted(self):
        """A secret stored as ``False`` (boolean) is redacted."""
        cfg = _ConfigLike(azure_api_key=False)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_falsy_empty_string_is_redacted(self):
        """A secret stored as ``""`` is redacted. Pre-DE-33 the empty
        string was preserved so the renderer could distinguish "no key
        set" from "key set but hidden" — but ``None`` is the canonical
        sentinel for "not configured" in the Config dataclass (most
        secret fields default to ``""``, not ``None``, so the empty-
        string "not configured" semantic was already ambiguous). DE-33
        unifies the contract: ``None`` → not configured; any other
        value (including ``""``) → redacted."""
        cfg = _ConfigLike(azure_api_key="")
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_none_value_is_preserved(self):
        """``None`` is preserved so the renderer can distinguish "not
        configured" from "configured but hidden". This is the one case
        where the original value is kept — any other value is masked."""
        cfg = _ConfigLike(cloud_api_key=None)
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_key"] is None

    def test_truthy_string_is_redacted(self):
        """The pre-DE-33 happy path: a truthy string secret is redacted."""
        cfg = _ConfigLike(cloud_api_key="sk-real-key-12345")
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_key"] == _REDACTED_SENTINEL

    def test_real_key_value_does_not_leak(self):
        """Grep the full sanitized dict: no real key value should
        appear anywhere in the serialized output (SEC-003 regression
        guard, now with the falsy-value fix)."""
        real_value = "sk-unique-marker-12345"
        cfg = _ConfigLike(
            cloud_api_key=real_value,
            azure_api_key=0,  # falsy — would have leaked pre-DE-33
            oauth_token=False,  # falsy — would have leaked pre-DE-33
        )
        out = _sanitize_config_for_ipc(cfg)
        serialized = str(out)
        assert real_value not in serialized


class TestSanitizePreservesNonSecretFields:
    """DE-33 regression guard: the sanitizer must not redact non-secret
    fields. Catches a potential over-redaction bug if the pattern
    denylist is too broad."""

    def test_non_secret_fields_preserved(self):
        cfg = _ConfigLike(
            hotkey="<f2>",
            language="fr",
            model_size="small.en",
            cloud_api_url="https://api.example.com",
            warn_password_paste=True,
            cloud_api_key="sk-real",
        )
        out = _sanitize_config_for_ipc(cfg)
        assert out["hotkey"] == "<f2>"
        assert out["language"] == "fr"
        assert out["model_size"] == "small.en"
        assert out["cloud_api_url"] == "https://api.example.com"
        assert out["warn_password_paste"] is True
        assert out["cloud_api_key"] == _REDACTED_SENTINEL


class TestSanitizeBackwardCompatWithExistingFields:
    """DE-33: the 5 fields in ``_SECRET_CONFIG_FIELDS`` are still
    redacted (backward compat — ``crash_recovery.py`` imports the
    frozenset for its own redaction path)."""

    @pytest.mark.parametrize("field_name", sorted(_SECRET_CONFIG_FIELDS))
    def test_existing_secret_field_still_redacted(self, field_name):
        cfg = _ConfigLike(**{field_name: "sk-real-key"})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL


class TestSanitizeContractWithRealConfig:
    """DE-33 (d): contract test — every field on the real ``Config``
    dataclass that matches a secret-name pattern is in
    ``_SECRET_CONFIG_FIELDS`` (i.e. the explicit allowlist covers
    every secret the maintainers have added so far).

    This is the "fail-loud when a new secret field is added without
    being listed" guard. If a future maintainer adds e.g.
    ``anthropic_api_key`` to ``Config`` WITHOUT also adding it to
    ``_SECRET_CONFIG_FIELDS``, this test will fail — alerting them
    that the field needs to be added (OR, equivalently, that they
    should rely on the pattern denylist — in which case the test
    asserts that the field IS still redacted by the sanitizer).

    Implementation note: the contract is "any field matching a secret
    pattern MUST be redacted by ``_sanitize_config_for_ipc``". The
    pattern denylist is the authoritative guard, so the test asserts
    the BEHAVIORAL contract (the field is redacted) rather than the
    STRUCTURAL contract (the field is in the frozenset). The latter
    would be too strict — the whole point of the denylist is that
    unlisted pattern-matching fields are still redacted.
    """

    def _real_config_fields(self):
        """Return the set of dataclass field names on the real Config."""
        from voice_typer.server.config import Config

        # ``Config`` is a dataclass — ``dataclasses.fields`` returns
        # the declared fields (not the runtime-only ``last_load_warnings``
        # attribute, which is intentionally excluded from ``asdict``).
        return {f.name for f in dataclasses.fields(Config())}

    def test_every_pattern_matching_config_field_is_redacted(self):
        """For each field on the real Config that matches a secret-name
        pattern, ``_sanitize_config_for_ipc`` MUST redact it (replace
        the value with the sentinel)."""
        from voice_typer.server.config import Config

        cfg = Config()
        # Set every pattern-matching field to a non-None value so the
        # redaction check is meaningful (None values are preserved).
        leaked: list[str] = []
        for field_name in self._real_config_fields():
            if not _is_secret_field_name(field_name):
                continue
            # Force the field to a non-None value, then sanitize.
            setattr(cfg, field_name, "sk-test-marker-for-redaction")
            out = _sanitize_config_for_ipc(cfg)
            if out[field_name] != _REDACTED_SENTINEL:
                leaked.append(field_name)
        assert not leaked, (
            f"Config fields matching secret-name patterns were NOT "
            f"redacted by _sanitize_config_for_ipc: {leaked}. Either "
            f"add them to _SECRET_CONFIG_FIELDS (explicit allowlist) "
            f"or extend _SECRET_FIELD_PATTERNS (pattern denylist)."
        )

    def test_no_benign_config_field_is_redacted(self):
        """Sanity check: at least one non-secret field on Config is
        preserved (e.g. ``hotkey``). Catches an over-redaction bug
        where the pattern denylist is too broad."""
        from voice_typer.server.config import Config

        cfg = Config()
        out = _sanitize_config_for_ipc(cfg)
        # ``hotkey`` is a plain config field — must not be redacted.
        assert "hotkey" in out, "hotkey field missing from sanitized output"
        assert out["hotkey"] != _REDACTED_SENTINEL, (
            "hotkey was redacted — the pattern denylist is too broad "
            "and is matching non-secret fields."
        )


# ══════════════════════════════════════════════════════════════════════════
# DE-34: COMMAND_COSTS covers every registered command
# ══════════════════════════════════════════════════════════════════════════


class TestCommandCostsContract:
    """DE-34: every command in ``_COMMAND_REGISTRY`` has an explicit
    ``COMMAND_COSTS`` entry.

    Pre-DE-34 the map listed only 5 commands; every other dispatched
    command fell through to ``DEFAULT_COST = 1``, including expensive
    operations like ``delete_model``, ``run_prewarm``,
    ``test_llm_connection``, ``export_diagnostics``, ``clear_history``,
    ``microphone_test_start``. A buggy or hostile client could fire
    200/s of any unlisted expensive command. The map now lists EVERY
    registered command so a future command added to
    ``_COMMAND_REGISTRY`` without a cost entry fails this test.
    """

    def test_every_registered_command_has_explicit_cost(self):
        """For each command in ``_COMMAND_REGISTRY``, assert it has an
        explicit entry in ``COMMAND_COSTS``. Fails with a clear list
        of missing commands if any are absent."""
        registered = set(IPCServer._COMMAND_REGISTRY.keys())
        listed = set(COMMAND_COSTS.keys())
        missing = registered - listed
        assert not missing, (
            f"Commands registered in _COMMAND_REGISTRY but missing "
            f"from COMMAND_COSTS: {sorted(missing)}. Each registered "
            f"command MUST have an explicit cost entry — add them to "
            f"COMMAND_COSTS in voice_typer/server/ipc/rate_limiter.py. "
            f"Cost tiers: 1=cheap read, 2=small write, 3=compute, "
            f"5=starts long-lived resource, 10=heavy I/O, 20=very "
            f"heavy, 50=network-saturating download."
        )

    def test_command_costs_does_not_list_unknown_commands(self):
        """Sanity check: ``COMMAND_COSTS`` should not contain commands
        that aren't in ``_COMMAND_REGISTRY`` — that would indicate a
        typo or a stale entry pointing at a removed command."""
        registered = set(IPCServer._COMMAND_REGISTRY.keys())
        listed = set(COMMAND_COSTS.keys())
        stale = listed - registered
        assert not stale, (
            f"COMMAND_COSTS contains entries for commands NOT in "
            f"_COMMAND_REGISTRY: {sorted(stale)}. These are stale "
            f"entries pointing at removed/renamed commands — remove "
            f"them from COMMAND_COSTS."
        )

    def test_all_costs_are_positive_integers(self):
        """Each cost must be a positive integer (>= 1). A cost of 0
        would let a client bypass the limiter entirely; a negative
        cost would corrupt the budget."""
        for cmd, cost in COMMAND_COSTS.items():
            assert isinstance(cost, int), (
                f"COMMAND_COSTS[{cmd!r}] = {cost!r} is not an int."
            )
            assert cost >= 1, (
                f"COMMAND_COSTS[{cmd!r}] = {cost} < 1 — costs must be "
                f"positive integers (the limiter clamps <1 to 1, but "
                f"the map should not encode that)."
            )


class TestCommandCostsPreserved:
    """DE-34: the 5 pre-existing entries are preserved (regression guard
    for the audit that expanded the map)."""

    def test_download_model_cost_50(self):
        assert COMMAND_COSTS["download_model"] == 50

    def test_import_model_cost_20(self):
        assert COMMAND_COSTS["import_model"] == 20

    def test_export_gdpr_bundle_cost_20(self):
        assert COMMAND_COSTS["export_gdpr_bundle"] == 20

    def test_delete_all_personal_data_cost_20(self):
        assert COMMAND_COSTS["delete_all_personal_data"] == 20

    def test_heartbeat_cost_1(self):
        assert COMMAND_COSTS["heartbeat"] == 1


class TestCommandCostsNewlyListed:
    """DE-34: spot-check a few of the newly-listed expensive commands
    that previously fell through to ``DEFAULT_COST = 1``.

    The exact cost values are heuristic (calibrated against the 200/s
    burst budget) — these tests pin the values so a future careless
    refactor doesn't silently revert them to 1.
    """

    @pytest.mark.parametrize(
        "cmd, min_cost",
        [
            # Heavy I/O or subprocess (cost >= 10).
            ("delete_model", 10),
            ("run_prewarm", 10),
            ("restart_app", 10),
            ("test_llm_connection", 10),
            ("resume_model_download", 10),
            ("export_diagnostics", 10),
            ("clear_history", 10),
            # Moderate (cost >= 5).
            ("quit_app", 5),
            ("shutdown", 5),
            ("onboarding_apply", 5),
            ("microphone_test_start", 5),
            # Light-moderate (cost >= 3).
            ("get_vocabulary_suggestions", 3),
            ("level_monitor_start", 3),
            # Small file writes / single-row mutations (cost >= 2).
            ("save_vocabulary", 2),
            ("save_templates", 2),
            ("delete_history", 2),
            ("restore_history", 2),
            ("force_cancel_transcription", 2),
            ("pause_model_download", 2),
            ("cancel_model_download", 2),
        ],
    )
    def test_expensive_command_has_elevated_cost(self, cmd, min_cost):
        assert COMMAND_COSTS.get(cmd, DEFAULT_COST) >= min_cost, (
            f"COMMAND_COSTS[{cmd!r}] = {COMMAND_COSTS.get(cmd)} < "
            f"{min_cost}. Pre-DE-34 this command fell through to "
            f"DEFAULT_COST=1, allowing 200/s of an expensive operation. "
            f"The DE-34 fix elevated it; do not regress."
        )


class TestRateLimiterUsesElevatedCost:
    """DE-34 behavioural guard: the limiter actually applies the
    elevated cost — a cost-10 command consumes 10 of the 200/s burst
    budget, not 1."""

    def test_cost_10_command_rejected_after_20_calls_in_burst_window(self):
        """With burst=200 and cost=10 (e.g. ``delete_model``), the
        limiter accepts at most 20 calls in any 1-second window
        (20 * 10 = 200 = burst cap). The 21st call is rejected.

        Pre-DE-34 ``delete_model`` had cost=1 (DEFAULT_COST fallthrough),
        so the limiter accepted 200 calls/s — exactly the bug DE-34
        fixes.
        """
        # ``sustained_per_sec`` is the TOTAL budget over the 10s window
        # (the parameter name is misleading — it's a count, not a rate).
        # Set it high so only the burst check trips in this test.
        limiter = _RateLimiter(
            burst=200, sustained_per_sec=10_000, window=10.0
        )
        accepted = 0
        # 25 calls at t=0 — should accept 20 (20*10=200=burst), reject 5.
        for _ in range(25):
            if limiter.allow(command="delete_model", now=0.0):
                accepted += 1
        assert accepted == 20, (
            f"Expected 20 acceptances (burst=200 / cost=10 = 20), got "
            f"{accepted}. The rate limiter is not applying the elevated "
            f"COMMAND_COSTS['delete_model'] cost."
        )

    def test_cost_1_command_accepted_200_times_in_burst_window(self):
        """Sanity check: a cost-1 command (e.g. ``heartbeat``) still
        gets the full 200/s burst budget. Catches a regression where
        the cost map is applied incorrectly (e.g. every command gets
        cost=10)."""
        limiter = _RateLimiter(
            burst=200, sustained_per_sec=10_000, window=10.0
        )
        accepted = 0
        for _ in range(205):
            if limiter.allow(command="heartbeat", now=0.0):
                accepted += 1
        assert accepted == 200, (
            f"Expected 200 acceptances (cost=1), got {accepted}."
        )


# ══════════════════════════════════════════════════════════════════════════
# DE-35: _bound_history_offset caps at _HISTORY_OFFSET_MAX
# ══════════════════════════════════════════════════════════════════════════


class TestHistoryOffsetMaxConstant:
    """DE-35: the ``_HISTORY_OFFSET_MAX`` constant exists and is set
    to 10_000_000 (the value named in the fix section)."""

    def test_offset_max_is_10_million(self):
        assert _HISTORY_OFFSET_MAX == 10_000_000, (
            f"Expected _HISTORY_OFFSET_MAX == 10_000_000 (per DE-35 "
            f"fix section), got {_HISTORY_OFFSET_MAX}."
        )


class TestBoundHistoryOffsetLowerBound:
    """DE-35: the existing ``max(0, v)`` floor is preserved."""

    def test_zero_unchanged(self):
        assert _bound_history_offset(0) == 0

    def test_negative_clamped_to_zero(self):
        assert _bound_history_offset(-5) == 0
        assert _bound_history_offset(-1) == 0
        assert _bound_history_offset(-999999) == 0

    def test_none_returns_zero(self):
        assert _bound_history_offset(None) == 0

    def test_non_numeric_returns_zero(self):
        assert _bound_history_offset("not-a-number") == 0
        assert _bound_history_offset([]) == 0
        assert _bound_history_offset({}) == 0

    def test_string_numeric_parsed(self):
        assert _bound_history_offset("100") == 100
        assert _bound_history_offset("0") == 0


class TestBoundHistoryOffsetUpperBound:
    """DE-35: the new upper cap at ``_HISTORY_OFFSET_MAX``."""

    def test_within_bounds_unchanged(self):
        assert _bound_history_offset(100) == 100
        assert _bound_history_offset(1000) == 1000
        assert _bound_history_offset(100_000) == 100_000
        assert _bound_history_offset(1_000_000) == 1_000_000

    def test_at_max_unchanged(self):
        assert _bound_history_offset(_HISTORY_OFFSET_MAX) == _HISTORY_OFFSET_MAX

    def test_above_max_clamped_to_max(self):
        assert _bound_history_offset(_HISTORY_OFFSET_MAX + 1) == _HISTORY_OFFSET_MAX
        assert _bound_history_offset(999_999_999_999) == _HISTORY_OFFSET_MAX

    def test_python_bigint_clamped_to_max(self):
        """Python big-ints are unbounded — without the cap, a 5000-digit
        int would pass the ``max(0, v)`` clamp and reach SQLite's OFFSET
        clause, forcing a wasteful row-skip scan. DE-35 caps it.

        (Python 3.11+ defaults to a 4300-digit limit on int↔str
        conversion; we use 4000 digits to stay under the default but
        still vastly exceed ``_HISTORY_OFFSET_MAX`` = 10_000_000.)"""
        import sys

        # Bump the int-conversion digit limit so a 5000-digit literal
        # can be constructed. Restore the original on exit so the test
        # doesn't pollute the process-wide state for other tests.
        original_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(10_000)
            huge = int("9" * 5000)
            assert huge > _HISTORY_OFFSET_MAX
            assert _bound_history_offset(huge) == _HISTORY_OFFSET_MAX
        finally:
            sys.set_int_max_str_digits(original_limit)

    def test_float_above_max_clamped_to_max(self):
        """Floats are converted to int via ``int(raw)``; a float above
        the cap is clamped."""
        assert _bound_history_offset(99_999_999.5) == _HISTORY_OFFSET_MAX

    def test_string_above_max_clamped_to_max(self):
        assert _bound_history_offset("999999999999") == _HISTORY_OFFSET_MAX


class TestBoundHistoryLimitUnaffected:
    """DE-35 regression guard: the existing ``_bound_history_limit``
    behavior (lower bound 1, upper bound 500) is unchanged by the
    offset cap addition."""

    def test_limit_max_unchanged(self):
        from voice_typer.server.ipc.history_bounds import _HISTORY_LIMIT_MAX

        assert _HISTORY_LIMIT_MAX == 500

    def test_limit_above_max_clamped(self):
        assert _bound_history_limit(1_000_000) == 500

    def test_limit_zero_clamped_to_one(self):
        assert _bound_history_limit(0) == 1

    def test_limit_negative_clamped_to_one(self):
        assert _bound_history_limit(-5) == 1


# ══════════════════════════════════════════════════════════════════════════
# DE-36: _validate_dict_payload emits namespaced error codes
# ══════════════════════════════════════════════════════════════════════════


class TestNamespacedInvalidPayload:
    """DE-36: non-dict payload emits ``code=client.invalid_payload``."""

    def test_non_dict_payload_returns_namespaced_code(self):
        validated, error = _validate_dict_payload("not-a-dict", {})
        assert validated is None
        assert error is not None
        assert error["type"] == "error"
        assert error["data"]["code"] == "client.invalid_payload", (
            f"Expected 'client.invalid_payload' (namespaced form per "
            f"DE-36 / G4-M-22), got {error['data']['code']!r}."
        )

    def test_non_dict_payload_also_emits_legacy_code(self):
        """DE-36: the legacy bare form is preserved in ``legacy_code``
        for one release cycle so the renderer (and existing tests)
        can migrate without a hard cutover."""
        _, error = _validate_dict_payload([], {})
        assert error["data"]["legacy_code"] == "invalid_payload"

    @pytest.mark.parametrize(
        "bad_payload",
        ["a-string", 42, 3.14, ["a", "list"], ("a", "tuple"), {1, 2, 3}],
    )
    def test_various_non_dict_payloads(self, bad_payload):
        _, error = _validate_dict_payload(bad_payload, {})
        assert error["data"]["code"] == "client.invalid_payload"
        assert error["data"]["legacy_code"] == "invalid_payload"

    def test_max_payload_bytes_violation_returns_namespaced_code(self):
        """The ``max_payload_bytes`` rule (DoS guard) emits the
        namespaced ``client.invalid_payload`` when the payload exceeds
        the cap."""
        schema = {
            "x": {"type": str, "required": False, "max_payload_bytes": 10},
        }
        # ``data`` serializes to ~30 bytes — well above the 10-byte cap.
        _, error = _validate_dict_payload({"x": "this-is-way-too-long"}, schema)
        assert error["data"]["code"] == "client.invalid_payload"
        assert error["data"]["legacy_code"] == "invalid_payload"


class TestNamespacedInvalidField:
    """DE-36: wrong-type field emits ``code=client.invalid_field``."""

    def test_wrong_type_returns_namespaced_code(self):
        validated, error = _validate_dict_payload(
            {"model": 123},
            {"model": {"type": str, "required": True}},
        )
        assert validated is None
        assert error["data"]["code"] == "client.invalid_field", (
            f"Expected 'client.invalid_field', got {error['data']['code']!r}."
        )
        assert error["data"]["legacy_code"] == "invalid_field"
        assert error["data"]["field"] == "model"

    def test_wrong_type_with_tuple_type_annotation(self):
        """When the schema's ``type`` is a tuple (e.g. ``(str, type(None))``),
        the error message lists all allowed types — the code is still
        the namespaced ``client.invalid_field``."""
        validated, error = _validate_dict_payload(
            {"mic_id": 123},
            {"mic_id": {"type": (str, type(None)), "required": True}},
        )
        assert validated is None
        assert error["data"]["code"] == "client.invalid_field"
        assert error["data"]["legacy_code"] == "invalid_field"
        assert error["data"]["field"] == "mic_id"
        # The message should list both allowed types.
        assert "str" in error["data"]["message"]
        assert "NoneType" in error["data"]["message"]

    def test_max_value_len_violation_returns_namespaced_code(self):
        """The ``max_value_len`` rule emits the namespaced
        ``client.invalid_field`` when a string value is too long."""
        schema = {
            "name": {"type": str, "required": True, "max_value_len": 5},
        }
        _, error = _validate_dict_payload({"name": "way-too-long-string"}, schema)
        assert error["data"]["code"] == "client.invalid_field"
        assert error["data"]["legacy_code"] == "invalid_field"
        assert error["data"]["field"] == "name"


class TestNamespacedMissingField:
    """DE-36: missing required field emits ``code=client.missing_field``."""

    def test_missing_required_returns_namespaced_code(self):
        validated, error = _validate_dict_payload(
            {},
            {"model": {"type": str, "required": True}},
        )
        assert validated is None
        assert error["data"]["code"] == "client.missing_field", (
            f"Expected 'client.missing_field', got {error['data']['code']!r}."
        )
        assert error["data"]["legacy_code"] == "missing_field"
        assert error["data"]["field"] == "model"


class TestValidationHappyPathUnaffected:
    """DE-36 regression guard: the happy path (valid payload) still
    returns ``(validated_dict, None)`` with no error envelope."""

    def test_valid_payload_returns_validated_dict(self):
        validated, error = _validate_dict_payload(
            {"model": "small.en", "hotkey": "<f2>"},
            {
                "model": {"type": str, "required": True},
                "hotkey": {"type": str, "required": False, "default": "<f9>"},
            },
        )
        assert error is None
        assert validated == {"model": "small.en", "hotkey": "<f2>"}

    def test_optional_field_uses_default(self):
        validated, error = _validate_dict_payload(
            {"model": "small.en"},
            {
                "model": {"type": str, "required": True},
                "hotkey": {"type": str, "required": False, "default": "<f9>"},
            },
        )
        assert error is None
        assert validated == {"model": "small.en", "hotkey": "<f9>"}

    def test_clamp_range_coerces_numeric_value(self):
        validated, error = _validate_dict_payload(
            {"duration_ms": 99_999_999},
            {
                "duration_ms": {
                    "type": int,
                    "required": True,
                    "clamp_range": (0, 86_400_000),
                },
            },
        )
        assert error is None
        assert validated == {"duration_ms": 86_400_000}


class TestNamespacedCodesRegistered:
    """DE-36: the namespaced codes emitted by ``_validate_dict_payload``
    are all registered in ``ERROR_CODES`` (the contract test in
    ``tests/test_error_codes_registry.py`` is the canonical guard,
    but this is a self-contained sanity check)."""

    @pytest.mark.parametrize(
        "code",
        ["client.invalid_payload", "client.invalid_field", "client.missing_field"],
    )
    def test_namespaced_code_in_registry(self, code):
        assert code in ERROR_CODES, (
            f"Namespaced code {code!r} emitted by _validate_dict_payload "
            f"is NOT in ERROR_CODES. Add it to the registry in "
            f"voice_typer/server/ipc/validation.py."
        )
