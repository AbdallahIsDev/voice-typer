"""
Direct adversarial tests for ``voice_typer.server.ipc.history_bounds``.
SEC-010 history-bounds clamping. Python big-ints are unbounded, so
"""

from __future__ import annotations

import pytest
from voice_typer.server.ipc.history_bounds import (
    _HISTORY_LIMIT_DEFAULT,
    _HISTORY_LIMIT_MAX,
    _HISTORY_OFFSET_MAX,
    _REDACTED_SENTINEL,
    _SECRET_FIELD_PATTERNS,
    HISTORY_OFFSET_LIMIT,
    _bound_history_limit,
    _bound_history_offset,
    _is_secret_field_name,
    _sanitize_config_for_ipc,
    deep_offset_message,
)

# The real ``Config`` is a dataclass with ~80 fields; constructing one


class _ConfigLike:
    """Plain object exposing ``__dict__`` for the sanitizer."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestSanitizePatternDenylistDirect:
    """Drive ``_sanitize_config_for_ipc`` with pattern-denylist hits."""

    @pytest.mark.parametrize(
        "field_name, value",
        [
            ("azure_api_key", "sk-azure-deadbeef"),
            ("oauth_token", "oauth-token-abc123"),
            ("refresh_token", "refresh-token-xyz789"),
        ],
    )
    def test_unlisted_secret_field_is_redacted(self, field_name, value):
        """A secret-bearing field NOT in ``_SECRET_CONFIG_FIELDS`` must"""
        cfg = _ConfigLike(**{field_name: value})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL, (
            f"Pattern-denylist failed for {field_name!r}: expected "
            f"{_REDACTED_SENTINEL!r}, got {out[field_name]!r}. The "
            f"field matches a _SECRET_FIELD_PATTERNS entry and must be "
            f"redacted even though it's not in _SECRET_CONFIG_FIELDS."
        )

    def test_pattern_denylist_actually_classifies_each_name(self):
        """secret by ``_is_secret_field_name`` (i.e. the pattern denylist"""
        for name in ("azure_api_key", "oauth_token", "refresh_token"):
            assert _is_secret_field_name(name) is True, (
                f"{name!r} must be classified as secret by either _SECRET_CONFIG_FIELDS or _SECRET_FIELD_PATTERNS."
            )

    def test_real_key_value_does_not_leak_anywhere(self):
        """Grep the full sanitized dict: none of the three real values"""
        cfg = _ConfigLike(
            azure_api_key="sk-azure-DO-NOT-LEAK-1",
            oauth_token="oauth-DO-NOT-LEAK-2",
            refresh_token="refresh-DO-NOT-LEAK-3",
        )
        out = _sanitize_config_for_ipc(cfg)
        serialized = repr(out)
        assert "sk-azure-DO-NOT-LEAK-1" not in serialized
        assert "oauth-DO-NOT-LEAK-2" not in serialized
        assert "refresh-DO-NOT-LEAK-3" not in serialized


class TestSanitizeFalsySecretValuesDirect:
    """Covers acceptance criterion (2): ``0`` / ``False`` secret values"""

    @pytest.mark.parametrize(
        "value",
        [0, False],
        ids=["int-zero", "bool-false"],
    )
    def test_falsy_secret_is_redacted(self, value):
        """``0`` / ``False`` are set values and must be masked."""
        cfg = _ConfigLike(azure_api_key=value)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL, (
            f"Falsy secret value {value!r} must be masked to {_REDACTED_SENTINEL!r}, not leaked verbatim."
        )

    def test_empty_string_is_preserved_as_unset_sentinel(self):
        """``\"\"`` is the \"no key set\" sentinel, preserved verbatim so"""
        cfg = _ConfigLike(azure_api_key="")
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == ""

    def test_none_value_is_preserved(self):
        """``None`` is the one exception: it's preserved so the renderer"""
        cfg = _ConfigLike(azure_api_key=None)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] is None


class TestBoundHistoryOffsetBoundariesDirect:
    """Covers acceptance criteria (3)-(7): the offset clamp must"""

    def test_negative_clamped_to_zero(self):
        # Criterion (3).
        assert _bound_history_offset(-1) == 0

    def test_zero_preserved(self):
        # Criterion (4).
        assert _bound_history_offset(0) == 0

    def test_at_max_preserved(self):
        # Criterion (5).
        assert _bound_history_offset(10_000_000) == 10_000_000
        assert _bound_history_offset(10_000_000) == _HISTORY_OFFSET_MAX

    def test_one_above_max_clamped(self):
        # Criterion (6).
        assert _bound_history_offset(10_000_001) == 10_000_000

    def test_python_bigint_clamped_to_max(self):
        """``max(0, v)`` floor and reach SQLite's ``OFFSET`` clause,"""
        huge = 2**10000
        assert huge > _HISTORY_OFFSET_MAX, "Sanity: 2**10000 must vastly exceed _HISTORY_OFFSET_MAX."
        assert _bound_history_offset(huge) == _HISTORY_OFFSET_MAX


class TestSharedOffsetLimitPin:
    """The IPC bounder and the DB guard share ``HISTORY_OFFSET_LIMIT``."""

    def test_shared_limit_is_999(self):
        assert HISTORY_OFFSET_LIMIT == 999

    def test_shared_limit_sits_below_dos_cap(self):
        assert HISTORY_OFFSET_LIMIT < _HISTORY_OFFSET_MAX

    def test_deep_offset_message_names_cursor_alternative(self):
        msg = deep_offset_message(1000)
        assert "999" in msg
        assert "1000" in msg
        assert "cursor pagination" in msg


class TestBoundHistoryLimitBoundariesDirect:
    """Covers acceptance criterion (8): the limit clamp must preserve"""

    def test_at_max_preserved(self):
        assert _bound_history_limit(_HISTORY_LIMIT_MAX) == _HISTORY_LIMIT_MAX
        assert _bound_history_limit(500) == 500

    def test_one_above_max_clamped(self):
        assert _bound_history_limit(_HISTORY_LIMIT_MAX + 1) == _HISTORY_LIMIT_MAX
        assert _bound_history_limit(501) == 500

    def test_zero_clamped_to_one(self):
        """Floor is 1, not 0, a zero limit must never reach SQLite"""
        assert _bound_history_limit(0) == 1

    def test_default_is_50(self):
        """``None`` / non-numeric inputs fall back to the default"""
        assert _HISTORY_LIMIT_DEFAULT == 50
        assert _bound_history_limit(None) == _HISTORY_LIMIT_DEFAULT


class TestSecretFieldPatternsRobustnessDirect:
    """Covers acceptance criterion (9): the ``!_api_key`` suffix"""

    @pytest.mark.parametrize(
        "field_name",
        [
            # The three acceptance-criterion names.
            "azure_api_key",
            "oauth_token",
            "refresh_token",
            # Conventional vendor API-key suffix variants, these
            "anthropic_api_key",
            "assemblyai_api_key",
            "cloud_api_key",
            "deepgram_api_key",
            "elevenlabs_api_key",
            "groq_api_key",
            "llm_api_key",
            "mistral_api_key",
            "openai_api_key",
            "replicate_api_key",
            "whisper_api_key",
            # Camel-cased / mixed-vendor variants still match because
            "Acme_api_key",
            # The generic ``!_key`` suffix catches crypto key blobs
            "private_key",
            "secret_key",
            "signing_key",
            "hmac_key",
            "aes_key",
            "encryption_key",
        ],
    )
    def test_api_key_suffix_variants_are_redacted(self, field_name):
        """Every ``*_api_key`` / ``*_key`` / ``*_token`` /"""
        assert _is_secret_field_name(field_name) is True, (
            f"{field_name!r} should match a _SECRET_FIELD_PATTERNS entry (suffix or exact match) but does not."
        )

    def test_api_key_suffix_pattern_is_present(self):
        """Defense-in-depth: the ``!_api_key`` suffix pattern itself"""
        assert "!_api_key" in _SECRET_FIELD_PATTERNS, (
            "The '!_api_key' suffix pattern is missing from "
            "_SECRET_FIELD_PATTERNS, every *_api_key field would "
            "leak verbatim to the IPC client."
        )

    def test_no_benign_field_matches_api_key_suffix(self):
        """A field that merely CONTAINS the substring ``api_key`` but"""
        assert _is_secret_field_name("api_keyring_status") is False
        assert _is_secret_field_name("cloud_api_url") is False, (
            "cloud_api_url ends in '_url', not '_api_key', must not "
            "be redacted (the renderer needs the URL to display it)."
        )
        assert _is_secret_field_name("warn_password_paste") is False, (
            "warn_password_paste ends in '_paste', not '_password', "
            "the boolean flag is NOT a secret and must be echoed so "
            "the renderer can render the toggle UI."
        )


class TestNoRealConfigFileIsWrittenDirect:
    """Covers acceptance criterion (10): the sanitizer must NOT write"""

    def test_sanitizer_leaves_no_artifacts_in_tmp_path(self, tmp_path):
        """``config.__dict__``, it must never touch the filesystem."""
        cfg = _ConfigLike(
            azure_api_key="sk-azure-tmp-path-test",
            oauth_token="oauth-tmp-path-test",
            refresh_token="refresh-tmp-path-test",
        )
        out = _sanitize_config_for_ipc(cfg)
        # The sanitizer must have returned a redacted dict...
        assert out["azure_api_key"] == _REDACTED_SENTINEL
        # ...and written nothing to disk.
        assert list(tmp_path.iterdir()) == [], (
            "Expected tmp_path to be empty, the sanitizer must NOT "
            "write a config.json or any other artifact as a "
            "side-effect of redacting secrets."
        )

    def test_sanitizer_does_not_import_or_instantiate_real_config(self):
        """The sanitizer never reaches for the real ``Config`` class"""
        cfg = _ConfigLike(azure_api_key="sk-only-this-field")
        out = _sanitize_config_for_ipc(cfg)
        assert set(out.keys()) == {"azure_api_key"}
        assert out["azure_api_key"] == _REDACTED_SENTINEL


class TestBoundBoolRejectionDirect:
    """Bool inputs are toggles, not pagination counts; bounders reject them."""

    @pytest.mark.parametrize("raw", [True, False], ids=["true", "false"])
    def test_limit_bool_falls_back_to_default(self, raw):
        assert _bound_history_limit(raw) == _HISTORY_LIMIT_DEFAULT

    @pytest.mark.parametrize("raw", [True, False], ids=["true", "false"])
    def test_offset_bool_falls_back_to_zero(self, raw):
        assert _bound_history_offset(raw) == 0


class TestBoundIntCastDirect:
    """Numeric strings coerce; non-numeric strings fall back to defaults."""

    def test_limit_numeric_string_coerces(self):
        assert _bound_history_limit("25") == 25

    def test_limit_non_numeric_string_falls_back(self):
        assert _bound_history_limit("not-a-number") == _HISTORY_LIMIT_DEFAULT

    def test_offset_numeric_string_coerces(self):
        assert _bound_history_offset("10") == 10

    def test_offset_non_numeric_string_falls_back(self):
        assert _bound_history_offset("not-a-number") == 0


class TestExtractHistoryCursorBeforeIdDirect:
    """Non-numeric ``before_id`` is a precise client error, not a crash."""

    def _mixin(self):
        from voice_typer.server.handlers.history_handlers import HistoryHandlersMixin

        return HistoryHandlersMixin.__new__(HistoryHandlersMixin)

    def test_non_numeric_before_id_is_invalid_field(self):
        mixin = self._mixin()
        resp: dict = {"type": "", "data": {}}
        result = mixin._extract_history_cursor({"before_timestamp": None, "before_id": "not-a-number"}, resp)
        assert isinstance(result, dict)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_field"
        assert result["data"]["field"] == "before_id"

    def test_negative_before_id_is_invalid_field(self):
        mixin = self._mixin()
        resp: dict = {"type": "", "data": {}}
        result = mixin._extract_history_cursor({"before_timestamp": None, "before_id": -1}, resp)
        assert isinstance(result, dict)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_field"
        assert result["data"]["field"] == "before_id"

    def test_numeric_string_before_id_coerces(self):
        mixin = self._mixin()
        resp: dict = {"type": "", "data": {}}
        result = mixin._extract_history_cursor({"before_timestamp": None, "before_id": "42"}, resp)
        assert result == (None, 42)
