"""Config lifecycle / load / save method delegators."""

import threading
from typing import TYPE_CHECKING, Any, ClassVar, cast

from voice_typer.server.config._migration import _backup_before_downgrade_impl
from voice_typer.server.config._saving import (
    _enforce_windows_owner_only_acl,  # noqa: F401, re-exported for callers
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

if TYPE_CHECKING:  # pragma: no cover, typing-only, never imported at runtime
    from pathlib import Path

    from voice_typer.server.config import Config

__all__ = ["_ConfigLifecycleMixin"]


class _ConfigLifecycleMixin:
    """Lifecycle / load / save method delegators for ``Config``."""

    # class-level reference to an in-process mutation lock.
    _mutation_lock: ClassVar[Any] = None

    def __post_init__(self) -> None:
        """Initialize the transient non-field attributes."""
        # Use object.__setattr__ to bypass any frozen/dataclass
        object.__setattr__(self, "last_load_warnings", None)
        object.__setattr__(self, "_last_saved_bytes", None)
        object.__setattr__(self, "_dirty", True)
        object.__setattr__(self, "_secrets_routed_in_save", False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Track mutations to persisted dataclass fields via the"""
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
        , making the lock impossible to forget at the 10+ other
         ``config.save()`` call sites (``settings_controller``,
         ``hotkey_dispatcher``, ``model_manager``, ``recorder._persist_mic``,
         ``startup_sequence``, etc.).

         The reference is stored as an INSTANCE attribute (shadowing
         the ``ClassVar`` default of ``None``) so each ``Config``
          instance can have its own lock: multiple ``VoiceTyperApp``
         instances in the same process (rare but possible in tests)
         don't share a single global lock.

         Passing ``None`` clears the lock (disables locking).
        """
        # Use the instance dict directly so the ClassVar is shadowed
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
        """
        return _save_impl(cast("Config", self))

    def _save_with_mutation_lock(self) -> bool:
        """Acquire the mutation lock (if set); delegate to ``_save_unlocked``."""
        return _save_with_mutation_lock_impl(cast("Config", self))

    def _save_unlocked(self) -> bool:
        """Body of :meth:`save`: assumes both locks are held."""
        return _save_unlocked_impl(cast("Config", self))

    # back-compat alias: the original pre-refactor name was
    _save_locked = _save_unlocked

    def save_strict(self) -> None:
        """Save config to disk; raise RuntimeError on failure."""
        _save_strict_impl(cast("Config", self))

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults."""
        return _load_config(cls)

    @classmethod
    def _read_raw_json(cls, config_file) -> dict | None:
        """Read + parse ``config_file`` as JSON; return the parsed dict (or None)."""
        return _read_raw_json_impl(config_file)

    @classmethod
    def _filter_unknown_keys(cls, parsed: dict, config_file) -> dict:
        """Filter unknown keys from ``parsed``; log a WARNING for each dropped key."""
        return _filter_unknown_keys_impl(cls, parsed, config_file)

    @classmethod
    def _run_migrations(
        cls,
        data: dict[str, Any],
        loaded_version: Any,
        config_file,
    ) -> tuple[dict[str, Any], int, bool]:
        """Run forward schema migrations from ``loaded_version``."""
        return _run_migrations(data, loaded_version, config_file)

    @classmethod
    def _backup_before_migration(cls, config_file, loaded_version: Any) -> None:
        """Best-effort backup of ``config.json`` BEFORE any migration runs."""
        import voice_typer.server.config as _config_mod

        _config_mod._backup_before_migration_impl(config_file, loaded_version)

    @classmethod
    def _backup_before_downgrade(
        cls,
        config_file,
        loaded_version: Any,
        data: dict[str, Any],
    ) -> None:
        """Best-effort versioned backup when an older build loads a"""
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
        """Build a ``{field_name: expected_type}`` registry from the Config dataclass."""
        return _sanitization_derive_field_type_registry(cast("type[Config]", cls))

    @classmethod
    def _reset_invalid_enum_fields(cls, instance: "Config") -> None:
        """Reset invalid ``Literal[...]`` enum fields to their defaults."""
        return _reset_invalid_enum_fields_impl(cls, instance)

    @classmethod
    def _secret_field_names(cls) -> frozenset[str]:
        """Return the set of Config field names holding secrets."""
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
        """Reset ``field_name`` to its default value with a logged warning."""
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
        """Record a coercion warning and return the coerced value."""
        return _sanitization_warn_and_coerce(
            cast("type[Config]", cls), field_name, val, coerced, warnings, reason=reason
        )

    @classmethod
    def _validate_non_numeric_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Validate and coerce bool / str / int / float fields in loaded config data.

        migration layer: fixes up legacy on-disk values before dataclass construction.
        """
        return _sanitization_validate_non_numeric_fields(cast("type[Config]", cls), data)

    @property
    def config_dir(self) -> "Path":
        """The resolved per-user config directory."""
        import voice_typer.server.config as _cfg

        return _cfg._config_dir()
