"""Config lifecycle / load / save method delegators.

Continuation of the config monolith split: the ``Config``
lifecycle methods live here on the ``_ConfigLifecycleMixin`` as thin
delegators to the sibling leaf modules. ``Config`` (in
``config/__init__.py``) inherits them via multiple inheritance from
the ``_ConfigSchema`` field base + this mixin — callers see the same
public API (``cfg.save()``, ``Config.load()``, ``cfg._secret_field_names()``,
``Config._coerce_streaming_fields(data)``, ...).

Method → impl mapping:

- ``__post_init__`` / ``__setattr__`` / ``set_mutation_lock`` —
  defined here (transient-attribute init + dirty-flag tracking +
  per-instance mutation-lock wiring),
- ``save`` / ``_save_with_mutation_lock`` / ``_save_unlocked`` /
  ``_save_locked`` (back-compat alias) / ``save_strict`` /
  ``_warmup_keyring_probe`` → ``config/_saving.py`` impls,
- ``load`` / ``_read_raw_json`` / ``_filter_unknown_keys`` →
  ``config/loader.py`` impls,
- ``_run_migrations`` / ``_backup_before_migration`` →
  ``config_internals/migrations.py`` impls,
- ``_backup_before_downgrade`` → ``config/_migration.py`` impl,
- ``_coerce_*`` / ``_validate_*`` (path/consent) →
  ``config/coercion.py`` impls,
- ``_derive_field_type_registry`` / ``_warn_and_reset`` /
  ``_warn_and_coerce`` / ``_validate_non_numeric_fields`` →
  ``config/sanitization.py`` impls,
- ``_reset_invalid_enum_fields`` / ``_secret_field_names`` →
  ``config/_schema.py`` impls,
- ``config_dir`` property → ``config_internals.paths._config_dir``.

Import-safety: this module is imported at the TOP of
``config/__init__.py``. Top-level imports only touch leaf modules
(``config_internals.*``, ``config/_saving``, ``config/_schema``,
``config/_migration``, ``config/coercion``, ``config/loader``,
``config/sanitization``) — never the ``voice_typer.server.config``
package itself (circular).
"""

import threading
from typing import TYPE_CHECKING, Any, ClassVar, cast

from voice_typer.server.config._migration import _backup_before_downgrade_impl
from voice_typer.server.config._saving import (
    _enforce_windows_owner_only_acl,  # noqa: F401 — re-exported for callers
    _save_impl,
    _save_strict_impl,
    _save_unlocked_impl,
    _save_with_mutation_lock_impl,
    _warmup_keyring_probe_impl,
)
from voice_typer.server.config._schema import (
    _reset_invalid_enum_fields_impl,
    _secret_field_names_impl,
)
from voice_typer.server.config.coercion import (
    _coerce_max_recording_time,
    _coerce_streaming_fields,
    _validate_corrections_path,
    _validate_model_path,
    _validate_privacy_consents,
    _validate_qwen_model_path,
)
from voice_typer.server.config.loader import (
    _filter_unknown_keys_impl,
    _load_config,
    _read_raw_json_impl,
)
from voice_typer.server.config.sanitization import (
    _derive_field_type_registry as _sanitization_derive_field_type_registry,
    _validate_non_numeric_fields as _sanitization_validate_non_numeric_fields,
    _warn_and_coerce as _sanitization_warn_and_coerce,
    _warn_and_reset as _sanitization_warn_and_reset,
)
from voice_typer.server.config_internals.migrations import (
    _run_migrations,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only, never imported at runtime
    from pathlib import Path

    from voice_typer.server.config import Config

__all__ = ["_ConfigLifecycleMixin"]


class _ConfigLifecycleMixin:
    """Lifecycle / load / save method delegators for ``Config``.

    Mixed into ``Config`` AFTER ``_ConfigSchema`` so the dataclass
    field declarations come first in the MRO. This class declares NO
    dataclass fields — the single annotated attribute
    (``_mutation_lock``) is a ``ClassVar`` so ``asdict()`` skips it.
    """

    # class-level reference to an in-process mutation lock.
    # When set (via :meth:`set_mutation_lock`), :meth:`save` acquires
    # this lock around the actual save work (:meth:`_save_unlocked`)
    # so two threads concurrently mutating and saving the Config
    # produce a consistent on-disk snapshot rather than a torn
    # half-and-half write. ``ClassVar`` ensures ``asdict(self)``
    # skips it (an ``RLock`` is not JSON-serializable and would
    # crash save()). Defaults to ``None`` for backward-compat —
    # freshly-constructed ``Config()`` instances (e.g. tests)
    # save without locking.
    #
    # NOTE: the annotation is a STRING because ``threading.RLock`` is
    # a callable factory (not a type) at runtime, so
    # ``threading.RLock | None`` would raise TypeError when evaluated.
    _mutation_lock: ClassVar[Any] = None

    def __post_init__(self) -> None:
        """Initialize the transient non-field attributes.

        - ``last_load_warnings``: was previously a dataclass field
          (which meant ``asdict()`` serialized it into config.json and
          stale warnings were read back on the next load). It's now a
          plain instance attribute so ``asdict()`` skips it; initialised
          to ``None`` here so freshly-constructed instances (e.g. the
          defaults fallback in :meth:`load`) have the attribute.
        - ``_last_saved_bytes``: cache of the bytes of the last
          successfully-persisted config.json. The next ``save()``
          compares its serialized content against this cache and skips
          the backup block + write entirely on a byte-identical resave.
        - ``_dirty``: True when a persisted field has been mutated
          since the last successful save (or since construction).
        - ``_secrets_routed_in_save``: set True by ``_save_unlocked``
          after it routes API-key fields through ``credential_store``;
          readers (``config_applier.apply_config``) check it to decide
          whether to run a redundant ``store_secret`` loop.
        """
        # Use object.__setattr__ to bypass any frozen/dataclass
        # machinery — Config is not frozen, but this is forward-
        # compatible if it ever is.
        object.__setattr__(self, "last_load_warnings", None)
        object.__setattr__(self, "_last_saved_bytes", None)
        object.__setattr__(self, "_dirty", True)
        object.__setattr__(self, "_secrets_routed_in_save", False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Track mutations to persisted dataclass fields via the
        ``_dirty`` flag.

        ``_dirty`` is set to True on every assignment to a persisted
        field (any attribute whose name does NOT start with ``_`` and
        is not the transient ``last_load_warnings`` attribute). Internal
        bookkeeping attributes (``_last_saved_bytes``, ``_dirty`` itself,
        ``_secrets_routed_in_save``, ``_mutation_lock``,
        ``last_load_warnings``) bypass the flag via ``object.__setattr__``
        at their call sites, so this override only fires for genuine
        user-facing field mutations (e.g. ``cfg.hotkey = "<f2>"`` or
        ``setattr(app.config, k, v)`` in ``apply_config``).

        The flag is checked at the top of ``_save_unlocked`` to skip
        the entire save (including ``asdict(self)`` + ``json.dumps``)
        when nothing has changed since the last successful save — the
        common case for ``set_config`` IPC round-trips that echo back
        the same config the server already has.
        """
        object.__setattr__(self, name, value)
        if not name.startswith("_") and name != "last_load_warnings":
            object.__setattr__(self, "_dirty", True)

    def set_mutation_lock(self, lock: "threading.RLock | None") -> None:
        """Register an in-process mutation lock for ``save()``.

        ``VoiceTyperApp`` owns a ``self._config_mutation_lock =
        threading.RLock()`` that ``service.apply_config`` and
        ``onboarding_apply`` acquire for the full read-modify-save
        sequence. Calling this method installs the same lock on the
        ``Config`` instance so :meth:`save` acquires it automatically
        — making the lock impossible to forget at the 10+ other
        ``config.save()`` call sites (``settings_controller``,
        ``hotkey_dispatcher``, ``model_manager``, ``recorder._persist_mic``,
        ``startup_sequence``, etc.).

        The reference is stored as an INSTANCE attribute (shadowing
        the ``ClassVar`` default of ``None``) so each ``Config``
        instance can have its own lock — multiple ``VoiceTyperApp``
        instances in the same process (rare but possible in tests)
        don't share a single global lock.

        Passing ``None`` clears the lock (disables locking).
        """
        # Use the instance dict directly so the ClassVar is shadowed
        # per-instance (rather than mutating the class attribute, which
        # would leak across instances).
        self.__dict__["_mutation_lock"] = lock

    @classmethod
    def _warmup_keyring_probe(cls) -> None:
        """Eagerly probe keyring availability once at app startup.

        See :func:`voice_typer.server.config._saving._warmup_keyring_probe_impl`.
        """
        _warmup_keyring_probe_impl()

    def save(self) -> bool:
        """Save config to disk atomically via temp file + os.replace.

        Returns True on success, False on failure. Errors are logged
        but NOT raised (never-raises contract relied upon by the IPC
        ``set_config`` handler). On Windows the config DIR's ACL is
        tightened BEFORE the cross-process lock is acquired; POSIX
        paths get 0o600/0o700 perms. API-key fields are routed through
        ``credential_store`` before serialization. When a mutation
        lock has been registered via :meth:`set_mutation_lock`, it is
        acquired around the actual save work.

        See :func:`voice_typer.server.config._saving._save_impl`.
        """
        return _save_impl(cast("Config", self))

    def _save_with_mutation_lock(self) -> bool:
        """Acquire the mutation lock (if set); delegate to ``_save_unlocked``.

        Assumes the cross-process file lock is already held (caller
        :meth:`save` acquires it). See
        :func:`voice_typer.server.config._saving._save_with_mutation_lock_impl`.
        """
        return _save_with_mutation_lock_impl(cast("Config", self))

    def _save_unlocked(self) -> bool:
        """Body of :meth:`save` — assumes both locks are held.

        Dirty-flag + byte-identical short-circuits, credential-store
        secret routing, best-effort ``config.json.bak`` backup, atomic
        write. See
        :func:`voice_typer.server.config._saving._save_unlocked_impl`.
        """
        return _save_unlocked_impl(cast("Config", self))

    # back-compat alias: the original pre-refactor name was
    # ``_save_locked`` (referring to the cross-process file lock).
    # Kept as an alias so any external callers / tests that still
    # reference the old name continue to work.
    _save_locked = _save_unlocked

    def save_strict(self) -> None:
        """Save config to disk; raise RuntimeError on failure.

        Wraps :meth:`save` for IPC handlers that must surface a silent
        disk failure as an IPC error rather than a successful-but-empty
        ack. See
        :func:`voice_typer.server.config._saving._save_strict_impl`.
        """
        _save_strict_impl(cast("Config", self))

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults.

        Expected corrupt-file failure modes (OSError,
        json.JSONDecodeError, TypeError, ValueError) fall back to
        defaults with a WARNING log + forensic quarantine of the
        corrupt file; genuine bugs (KeyError / AttributeError) and
        system-level failures propagate. The orchestrator body lives in
        :func:`voice_typer.server.config.loader._load_config`.
        """
        return _load_config(cls)

    # ── ``load()`` helpers (extracted from the original monolith body) ──

    @classmethod
    def _read_raw_json(cls, config_file) -> dict | None:
        """Read + parse ``config_file`` as JSON; return the parsed dict (or None).

        Uses :func:`_secure_read_text` (symlink-TOCTOU-safe). Returns
        ``None`` if the parsed JSON is not a dict. See
        :func:`voice_typer.server.config.loader._read_raw_json_impl`.
        """
        return _read_raw_json_impl(config_file)

    @classmethod
    def _filter_unknown_keys(cls, parsed: dict, config_file) -> dict:
        """Filter unknown keys from ``parsed``; log a WARNING for each dropped key.

        Unknown keys are silently dropped by the filter (with a
        once-per-(file, key-set) WARNING). See
        :func:`voice_typer.server.config.loader._filter_unknown_keys_impl`.
        """
        return _filter_unknown_keys_impl(cls, parsed, config_file)

    @classmethod
    def _run_migrations(
        cls,
        data: dict[str, Any],
        loaded_version: Any,
        config_file,
    ) -> tuple[dict[str, Any], int, bool]:
        """Run forward schema migrations from ``loaded_version``.

        Fail-soft semantics: do NOT bump schema_version on migrator
        exception; leave it at ``last_successful_version`` so the
        failed migration re-runs on next launch. See
        :func:`voice_typer.server.config_internals.migrations._run_migrations`.
        """
        return _run_migrations(data, loaded_version, config_file)

    @classmethod
    def _backup_before_migration(cls, config_file, loaded_version: Any) -> None:
        """Best-effort backup of ``config.json`` BEFORE any migration runs.

        Thin delegating wrapper so existing callers (and tests that
        call ``Config._backup_before_migration(config_file, 0)``
        directly) keep working unchanged. The impl is resolved through
        the ``config`` module namespace at call time (lazy import) so
        tests that monkeypatch ``config_mod._backup_before_migration_impl``
        intercept the delegation, and the io helpers inside the impl
        (``config_mod._secure_read_text`` / ``_secure_atomic_write`` /
        ``_prune_kept_backups``) stay patchable for the same reason.
        See
        :func:`voice_typer.server.config_internals.migrations._backup_before_migration_impl`
        for the full rationale (symlink-TOCTOU-safe read, atomic
        write, timestamped filename, retention cap of 3).
        """
        import voice_typer.server.config as _config_mod

        _config_mod._backup_before_migration_impl(config_file, loaded_version)

    @classmethod
    def _backup_before_downgrade(
        cls,
        config_file,
        loaded_version: Any,
        data: dict[str, Any],
    ) -> None:
        """Best-effort versioned backup when an older build loads a
        newer-version config.

        Called from :meth:`load` ONLY when ``loaded_version >
        _CURRENT_SCHEMA_VERSION``. Copies the on-disk ``config.json``
        (NOT the in-memory ``data``) to a timestamped
        ``config.json.v{loaded_version}-{ts}-{pid}-{ns}.bak`` and
        prunes to keep=3, then appends a non-blocking warning to
        ``data["_load_warnings"]``.

        Delegates to
        :func:`voice_typer.server.config._migration._backup_before_downgrade_impl`,
        whose parameter order is ``(cls, data, loaded_version,
        config_file)`` — the classmethod preserves the legacy public
        argument order above and forwards positionally.
        """
        _backup_before_downgrade_impl(cast("type[Config]", cls), data, loaded_version, config_file)

    @classmethod
    def _coerce_streaming_fields(cls, data: dict[str, Any]) -> None:
        """Coerce streaming_* fields with min/max clamping + invariant checks.

        Delegates to :func:`voice_typer.server.config.coercion._coerce_streaming_fields`.
        """
        _coerce_streaming_fields(data)

    @classmethod
    def _coerce_max_recording_time(cls, data: dict[str, Any]) -> None:
        """Clamp ``max_recording_time_seconds`` to valid range [300, 3600].

        Delegates to :func:`voice_typer.server.config.coercion._coerce_max_recording_time`.
        """
        _coerce_max_recording_time(data)

    @classmethod
    def _validate_model_path(cls, data: dict[str, Any]) -> None:
        """Validate ``model_size`` against :data:`ALLOWED_USER_MODELS`.

        Delegates to :func:`voice_typer.server.config.coercion._validate_model_path`.
        """
        _validate_model_path(data)

    @classmethod
    def _validate_qwen_model_path(cls, data: dict[str, Any]) -> None:
        """Validate ``qwen_model_path``: must be an existing directory if set.

        Delegates to :func:`voice_typer.server.config.coercion._validate_qwen_model_path`.
        """
        _validate_qwen_model_path(data)

    @classmethod
    def _validate_corrections_path(cls, data: dict[str, Any]) -> None:
        """Validate ``corrections_path``: must be an existing file if set.

        Delegates to :func:`voice_typer.server.config.coercion._validate_corrections_path`.
        """
        _validate_corrections_path(data)

    @classmethod
    def _validate_privacy_consents(cls, data: dict[str, Any]) -> None:
        """Warn the user about privacy implications when ``log_transcriptions`` is enabled.

        Delegates to :func:`voice_typer.server.config.coercion._validate_privacy_consents`.
        """
        _validate_privacy_consents(data)

    @classmethod
    def _derive_field_type_registry(cls) -> dict[str, type]:
        """Build a ``{field_name: expected_type}`` registry from the Config dataclass.

        Delegates to
        :func:`voice_typer.server.config.sanitization._derive_field_type_registry`.
        """
        return _sanitization_derive_field_type_registry(cast("type[Config]", cls))

    @classmethod
    def _reset_invalid_enum_fields(cls, instance: "Config") -> None:
        """Reset invalid ``Literal[...]`` enum fields to their defaults.

        Each reset appends a warning to ``instance.last_load_warnings``
        so the renderer can surface a "your config was corrected"
        toast. Best-effort: callers wrap this in try/except so a reset
        failure never breaks the load. See
        :func:`voice_typer.server.config._schema._reset_invalid_enum_fields_impl`.
        """
        return _reset_invalid_enum_fields_impl(cls, instance)

    @classmethod
    def _secret_field_names(cls) -> frozenset[str]:
        """Return the set of Config field names holding secrets.

        Sourced lazily from ``credential_store.PROVIDER_TO_CONFIG_FIELD``
        (fail-closed: re-raises on import failure instead of falling
        back to the stale hardcoded literal). See
        :func:`voice_typer.server.config._schema._secret_field_names_impl`.
        """
        return _secret_field_names_impl()

    @classmethod
    def _warn_and_reset(
        cls,
        field_name: str,
        val: Any,
        defaults: "Config",
        warnings: list[str],
        *,
        reason: str,
    ) -> Any:
        """Reset ``field_name`` to its default value with a logged warning.

        Delegates to
        :func:`voice_typer.server.config.sanitization._warn_and_reset`.
        The module-level function takes ``cls`` so subclass overrides of
        :meth:`_secret_field_names` are respected when redacting secret
        fields. Converted from ``@staticmethod`` to ``@classmethod`` so
        ``cls`` flows through; this is backward-compatible with the
        existing ``Config._warn_and_reset(field_name, val, ...)``
        call sites in ``tests/test_config_load_corruption.py``.
        """
        return _sanitization_warn_and_reset(
            cast("type[Config]", cls), field_name, val, defaults, warnings, reason=reason
        )

    @classmethod
    def _warn_and_coerce(
        cls,
        field_name: str,
        val: Any,
        coerced: Any,
        warnings: list[str],
        *,
        reason: str,
    ) -> Any:
        """Record a coercion warning and return the coerced value.

        Delegates to
        :func:`voice_typer.server.config.sanitization._warn_and_coerce`.
        Converted from ``@staticmethod`` to ``@classmethod`` so ``cls``
        flows through for the secret-field redaction lookup.
        """
        return _sanitization_warn_and_coerce(
            cast("type[Config]", cls), field_name, val, coerced, warnings, reason=reason
        )

    @classmethod
    def _validate_non_numeric_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Validate and coerce bool / str / int / float fields in loaded config data.

        This is a migration layer — NOT a duplicate of the type coercion
        that ``cls(**data)`` would do. Python dataclasses do NOT coerce
        ``1`` → ``True`` or ``"true"`` → ``True`` — they store the raw
        value as-is, which would then fail downstream type checks. This
        validator fixes up legacy on-disk configs BEFORE the dataclass
        constructor sees them. Delegates to
        :func:`voice_typer.server.config.sanitization._validate_non_numeric_fields`,
        which dispatches back through ``cls._warn_and_reset`` /
        ``cls._warn_and_coerce`` / ``cls._derive_field_type_registry``
        so subclass overrides of those methods are respected.
        """
        return _sanitization_validate_non_numeric_fields(cast("type[Config]", cls), data)

    @property
    def config_dir(self) -> "Path":
        """The resolved per-user config directory.

        ``_config_dir`` is looked up via the ``voice_typer.server.config``
        namespace at CALL time (not bound at import) so tests that
        monkeypatch ``voice_typer.server.config._config_dir`` (the shared
        ``tmp_config_dir`` fixture and the load-corruption suites) keep
        taking effect — mirroring how the pre-split monolith resolved
        the name from its own module globals.
        """
        import voice_typer.server.config as _cfg

        return _cfg._config_dir()
