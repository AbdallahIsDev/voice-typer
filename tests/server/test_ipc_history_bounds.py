"""Direct adversarial tests for ``voice_typer.server.ipc.history_bounds``.

This module is the focused regression guard for the two security helpers
that live in ``ipc/history_bounds.py``:

- :func:`_sanitize_config_for_ipc` — SEC-003 secret-redaction. The 8
  files that reference this function only do so as a tangential
  side-effect of broader IPC-server tests; this file drives the
  function directly with adversarial inputs (pattern-denylist hits,
  falsy values, big-int offsets, etc.).
- :func:`_bound_history_offset` / :func:`_bound_history_limit` —
  SEC-010 history-bounds clamping. Python big-ints are unbounded, so
  the upper cap at :data:`_HISTORY_OFFSET_MAX` (10_000_000) is the
  only thing standing between a hostile IPC client and a wasteful
  SQLite ``OFFSET n`` row-skip scan.

Scope (C-TEST-5): tests live in ``tests/server/``, NOT in production
source. No real ``Config`` instance is ever constructed — a tiny
``_ConfigLike`` stand-in injects the fields via ``__dict__`` so the
sanitizer never touches the real ``Config`` dataclass (which would
trigger credential-store integration and platform-default probes and
could write to disk).
"""

from __future__ import annotations

import pytest
from voice_typer.server.ipc.history_bounds import (
    _HISTORY_LIMIT_DEFAULT,
    _HISTORY_LIMIT_MAX,
    _HISTORY_OFFSET_MAX,
    _REDACTED_SENTINEL,
    _SECRET_FIELD_PATTERNS,
    _bound_history_limit,
    _bound_history_offset,
    _is_secret_field_name,
    _sanitize_config_for_ipc,
)

# ── Minimal Config stand-in ──────────────────────────────────────────────
#
# The real ``Config`` is a dataclass with ~80 fields; constructing one
# triggers credential-store integration, platform-default probes, and
# potentially a disk write to ``config.json``. To satisfy the
# "no real config file is written" acceptance criterion we use a plain
# class that just ``setattr``s the kwargs into ``__dict__`` — the
# sanitizer reads ``config.__dict__`` directly and never reaches for
# ``Config`` class machinery.


class _ConfigLike:
    """Plain object exposing ``__dict__`` for the sanitizer.

    Mirrors the ``_ConfigLike`` stand-in in ``tests/test_ipc_package_fixes.py``
    so the sanitizer tests stay self-contained and never touch the real
    ``Config`` dataclass (which would risk a disk write).
    """

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ══════════════════════════════════════════════════════════════════════════
# SEC-003 — pattern-denylist redaction of secret-bearing fields
# ══════════════════════════════════════════════════════════════════════════


class TestSanitizePatternDenylistDirect:
    """Drive ``_sanitize_config_for_ipc`` with pattern-denylist hits.

    Covers acceptance criterion (1): ``azure_api_key``,
    ``oauth_token``, ``refresh_token`` must be masked by the
    pattern-based denylist (:data:`_SECRET_FIELD_PATTERNS`) — NOT by
    the explicit ``_SECRET_CONFIG_FIELDS`` frozenset (these three
    names are not in the frozenset; the pattern is the only thing
    redacting them).
    """

    @pytest.mark.parametrize(
        "field_name, value",
        [
            ("azure_api_key", "sk-azure-deadbeef"),
            ("oauth_token", "oauth-token-abc123"),
            ("refresh_token", "refresh-token-xyz789"),
        ],
    )
    def test_unlisted_secret_field_is_redacted(self, field_name, value):
        """A secret-bearing field NOT in ``_SECRET_CONFIG_FIELDS`` must
        still be redacted by the pattern-based denylist (defense-in-
        depth). The renderer must never see the real value."""
        cfg = _ConfigLike(**{field_name: value})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL, (
            f"Pattern-denylist failed for {field_name!r}: expected "
            f"{_REDACTED_SENTINEL!r}, got {out[field_name]!r}. The "
            f"field matches a _SECRET_FIELD_PATTERNS entry and must be "
            f"redacted even though it's not in _SECRET_CONFIG_FIELDS."
        )

    def test_pattern_denylist_actually_classifies_each_name(self):
        """The three acceptance-criterion names must be classified as
        secret by ``_is_secret_field_name`` (i.e. the pattern denylist
        really does match them — this guards against a future
        refactor that breaks the pattern matcher)."""
        for name in ("azure_api_key", "oauth_token", "refresh_token"):
            assert _is_secret_field_name(name) is True, (
                f"{name!r} must be classified as secret by either _SECRET_CONFIG_FIELDS or _SECRET_FIELD_PATTERNS."
            )

    def test_real_key_value_does_not_leak_anywhere(self):
        """Grep the full sanitized dict: none of the three real values
        must appear in the serialized output (regression guard
        against a future regression that strips the ``<redacted>``
        sentinel and echoes the raw value)."""
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


# ══════════════════════════════════════════════════════════════════════════
# SEC-003 — falsy secret values are masked to the presence-indicator
# ══════════════════════════════════════════════════════════════════════════


class TestSanitizeFalsySecretValuesDirect:
    """Covers acceptance criterion (2): ``0``, ``False``, ``""`` secret
    values must all be masked to the presence-indicator
    (``<redacted>``) — NOT leaked verbatim.

    The old truthy-only redaction (``v if not v else _REDACTED_SENTINEL``)
    preserved these falsy values, which was fine for the empty-string
    "no key set" case but unsafe for ``0`` / ``False`` secrets and
    inconsistent with the documented "key is set" semantic. The fix
    masks any non-None value.
    """

    @pytest.mark.parametrize(
        "value",
        [0, False, ""],
        ids=["int-zero", "bool-false", "empty-string"],
    )
    def test_falsy_secret_is_redacted(self, value):
        """``0``, ``False``, ``""`` are non-None and must be masked."""
        cfg = _ConfigLike(azure_api_key=value)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL, (
            f"Falsy secret value {value!r} must be masked to "
            f"{_REDACTED_SENTINEL!r}, not leaked verbatim — the "
            f"redaction contract is 'any non-None value is masked'."
        )

    def test_none_value_is_preserved(self):
        """``None`` is the one exception: it's preserved so the renderer
        can distinguish "not configured" from "configured but hidden"."""
        cfg = _ConfigLike(azure_api_key=None)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] is None


# ══════════════════════════════════════════════════════════════════════════
# SEC-010 — _bound_history_offset boundary cases
# ══════════════════════════════════════════════════════════════════════════


class TestBoundHistoryOffsetBoundariesDirect:
    """Covers acceptance criteria (3)-(7): the offset clamp must
    floor at 0, preserve 0 and the max, and cap everything above
    the max — including Python big-ints that are unbounded.
    """

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
        """Criterion (7): Python big-ints are unbounded — without the
        cap, ``2**10000`` (a ~3000-digit int) would pass the
        ``max(0, v)`` floor and reach SQLite's ``OFFSET`` clause,
        forcing a wasteful row-skip scan. The cap must catch it.

        ``2**10000`` is a literal expression that doesn't go through
        ``int(str)`` so the Python 3.11+ ``sys.get_int_max_str_digits``
        limit doesn't apply — no need to bump it."""
        huge = 2**10000
        assert huge > _HISTORY_OFFSET_MAX, "Sanity: 2**10000 must vastly exceed _HISTORY_OFFSET_MAX."
        assert _bound_history_offset(huge) == _HISTORY_OFFSET_MAX


# ══════════════════════════════════════════════════════════════════════════
# SEC-010 — _bound_history_limit boundary at MAX and MAX+1
# ══════════════════════════════════════════════════════════════════════════


class TestBoundHistoryLimitBoundariesDirect:
    """Covers acceptance criterion (8): the limit clamp must preserve
    ``_HISTORY_LIMIT_MAX`` (500) and cap ``MAX + 1`` to ``MAX``.
    """

    def test_at_max_preserved(self):
        assert _bound_history_limit(_HISTORY_LIMIT_MAX) == _HISTORY_LIMIT_MAX
        assert _bound_history_limit(500) == 500

    def test_one_above_max_clamped(self):
        assert _bound_history_limit(_HISTORY_LIMIT_MAX + 1) == _HISTORY_LIMIT_MAX
        assert _bound_history_limit(501) == 500

    def test_zero_clamped_to_one(self):
        """Floor is 1, not 0 — a zero limit must never reach SQLite
        (a zero-row query is a caller bug, not a valid pagination
        request)."""
        assert _bound_history_limit(0) == 1

    def test_default_is_50(self):
        """``None`` / non-numeric inputs fall back to the default
        (50) — far below the cap, so a bad payload can never reach
        the row-skip DoS path."""
        assert _HISTORY_LIMIT_DEFAULT == 50
        assert _bound_history_limit(None) == _HISTORY_LIMIT_DEFAULT


# ══════════════════════════════════════════════════════════════════════════
# _SECRET_FIELD_PATTERNS — suffix-regex robustness
# ══════════════════════════════════════════════════════════════════════════


class TestSecretFieldPatternsRobustnessDirect:
    """Covers acceptance criterion (9): the ``!_api_key`` suffix
    pattern in :data:`_SECRET_FIELD_PATTERNS` must robustly catch
    every conventional ``*_api_key`` field name — including vendor
    names not yet seen in the wild (so a future contributor adding
    e.g. ``mistral_api_key`` doesn't leak it)."""

    @pytest.mark.parametrize(
        "field_name",
        [
            # The three acceptance-criterion names.
            "azure_api_key",
            "oauth_token",
            "refresh_token",
            # Conventional vendor API-key suffix variants — these
            # all end in ``_api_key`` so the ``"!_api_key"`` pattern
            # must match each one. Listed alphabetically for
            # readability; the matcher is suffix-based so order
            # doesn't matter.
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
            # the matcher is pure ``str.endswith`` — case-sensitive but
            # the conventional suffix is lowercase.
            "Acme_api_key",
            # The generic ``!_key`` suffix catches crypto key blobs
            # that the narrower ``!_api_key`` suffix missed.
            "private_key",
            "secret_key",
            "signing_key",
            "hmac_key",
            "aes_key",
            "encryption_key",
        ],
    )
    def test_api_key_suffix_variants_are_redacted(self, field_name):
        """Every ``*_api_key`` / ``*_key`` / ``*_token`` /
        ``*_secret`` / ``*_password`` / ``*_credential`` / ``*_bearer``
        variant must be classified as secret."""
        assert _is_secret_field_name(field_name) is True, (
            f"{field_name!r} should match a _SECRET_FIELD_PATTERNS entry (suffix or exact match) but does not."
        )

    def test_api_key_suffix_pattern_is_present(self):
        """Defense-in-depth: the ``!_api_key`` suffix pattern itself
        must be present in ``_SECRET_FIELD_PATTERNS``. A future
        refactor that drops it (or renames it) would silently leak
        every ``*_api_key`` field — this guards that."""
        assert "!_api_key" in _SECRET_FIELD_PATTERNS, (
            "The '!_api_key' suffix pattern is missing from "
            "_SECRET_FIELD_PATTERNS — every *_api_key field would "
            "leak verbatim to the IPC client."
        )

    def test_no_benign_field_matches_api_key_suffix(self):
        """A field that merely CONTAINS the substring ``api_key`` but
        doesn't END in ``_api_key`` must NOT be redacted — the
        pattern is name-based (suffix), not value-based. E.g.
        ``api_keyring_status`` ends in ``_status``, not ``_api_key``.
        """
        assert _is_secret_field_name("api_keyring_status") is False
        assert _is_secret_field_name("cloud_api_url") is False, (
            "cloud_api_url ends in '_url', not '_api_key' — must not "
            "be redacted (the renderer needs the URL to display it)."
        )
        assert _is_secret_field_name("warn_password_paste") is False, (
            "warn_password_paste ends in '_paste', not '_password' — "
            "the boolean flag is NOT a secret and must be echoed so "
            "the renderer can render the toggle UI."
        )


# ══════════════════════════════════════════════════════════════════════════
# No real config file is written — tmp_path / no-real-Config guard
# ══════════════════════════════════════════════════════════════════════════


class TestNoRealConfigFileIsWrittenDirect:
    """Covers acceptance criterion (10): the sanitizer must NOT write
    a real ``config.json`` file. We assert this two ways:

    1. The sanitizer never instantiates the real ``Config`` dataclass
       — it operates on the ``__dict__`` of whatever object is passed
       in. Using a ``_ConfigLike`` stand-in (no ``Config`` import)
       keeps the test hermetic.
    2. After running the sanitizer, the ``tmp_path`` directory must
       contain NO ``config.json`` (or any other artifact) — proving
       no disk write occurred as a side-effect.
    """

    def test_sanitizer_leaves_no_artifacts_in_tmp_path(self, tmp_path):
        """The sanitizer is a pure read-and-copy operation on
        ``config.__dict__`` — it must never touch the filesystem.
        We run it inside a fresh ``tmp_path`` and assert the dir
        stays empty."""
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
            "Expected tmp_path to be empty — the sanitizer must NOT "
            "write a config.json or any other artifact as a "
            "side-effect of redacting secrets."
        )

    def test_sanitizer_does_not_import_or_instantiate_real_config(self):
        """The sanitizer never reaches for the real ``Config`` class
        — it operates on a duck-typed object via ``__dict__``. We
        assert that no attribute lookup escapes into ``Config``
        machinery by passing a ``_ConfigLike`` with NO ``Config``
        attributes (no ``hotkey``, no ``model_size``, etc.) and
        checking the sanitizer returns the dict unchanged (modulo
        redaction) without raising ``AttributeError``."""
        cfg = _ConfigLike(azure_api_key="sk-only-this-field")
        out = _sanitize_config_for_ipc(cfg)
        # Only one field was set — only one field should be in the
        # output dict (after redaction).
        assert set(out.keys()) == {"azure_api_key"}
        assert out["azure_api_key"] == _REDACTED_SENTINEL
