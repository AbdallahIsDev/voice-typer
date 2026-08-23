"""Verify the config package split preserves structure + public API.

The ``voice_typer/server/config/`` package was carved out of a single
monolithic ``__init__.py`` (2,600+ lines) into focused sibling modules:

- ``_defaults.py``    — default-value constants + platform hotkey.
- ``_accessors.py``   — purge_user_data / purge_all_user_data etc.
- ``_migration.py``   — versioned downgrade-backup impl.
- ``_systemroot.py``  — systemroot validation re-export shim.
- ``_schema.py``      — ``_ConfigSchema`` dataclass base (ALL field
  declarations) + enum-reset / secret-field impls.
- ``_saving.py``      — save-path bodies (atomic write, ACL, warmup).
- ``_lifecycle.py``   — ``_ConfigLifecycleMixin`` delegator methods.
- ``coercion.py`` / ``loader.py`` / ``sanitization.py`` — load-time
  helpers (pre-existing).

The final ``Config`` combines schema + lifecycle via multiple
inheritance. These tests pin (a) the file/module layout, (b) every
symbol the package re-exports for backward compat, (c) the
monkeypatch-propagation contracts (impls resolve patched names via the
``config`` module namespace at call time), and (d) an end-to-end
construct → save → reload round-trip through the inherited API.
"""

from __future__ import annotations

import dataclasses
import inspect
import threading
from pathlib import Path

import pytest
from voice_typer.server import config as config_mod
from voice_typer.server.config import (
    Config,
    _lifecycle as config_lifecycle,
    _saving as config_saving,
    _schema as config_schema,
)

# ── Package layout ───────────────────────────────────────────────────────


class TestPackageLayout:
    def test_init_py_under_400_lines(self):
        """The split's headline goal: ``config/__init__.py`` stays a thin
        entry point under 400 lines."""
        init_py = Path(config_mod.__file__)
        assert init_py.name == "__init__.py"
        line_count = len(init_py.read_text(encoding="utf-8").splitlines())
        assert line_count < 400, (
            f"config/__init__.py regressed to {line_count} lines (target < 400) — "
            "logic must live in the focused sibling modules, not the entry point."
        )

    @pytest.mark.parametrize(
        "module_name",
        [
            "_accessors",
            "_defaults",
            "_lifecycle",
            "_migration",
            "_saving",
            "_schema",
            "_systemroot",
            "coercion",
            "loader",
            "sanitization",
        ],
    )
    def test_split_module_importable(self, module_name):
        import importlib

        mod = importlib.import_module(f"voice_typer.server.config.{module_name}")
        assert mod is not None


# ── Schema base class ────────────────────────────────────────────────────


class TestConfigSchemaBaseClassExtracted:
    def test_config_schema_base_class_extracted(self):
        """``_ConfigSchema`` is a dataclass in ``_schema`` and ``Config``
        inherits from it."""
        assert hasattr(config_schema, "_ConfigSchema")
        schema_cls = config_schema._ConfigSchema
        assert dataclasses.is_dataclass(schema_cls)
        assert issubclass(Config, schema_cls)
        # The field declarations live on the BASE, not on Config's own body.
        assert len(schema_cls.__dataclass_fields__) > 50
        assert not Config.__annotations__

    def test_config_inherits_field_default(self):
        """Field defaults declared on the schema base are visible on
        ``Config`` — both at class level and on constructed instances."""
        from voice_typer.server.config._defaults import DEFAULT_HOTKEY
        from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

        fields = Config.__dataclass_fields__
        assert fields["hotkey"].default == DEFAULT_HOTKEY
        assert fields["model_size"].default == DEFAULT_MODEL_SIZE
        cfg = Config()
        assert cfg.hotkey == DEFAULT_HOTKEY
        assert cfg.model_size == DEFAULT_MODEL_SIZE
        assert isinstance(cfg, config_schema._ConfigSchema)

    def test_schema_classvars_exposed_at_both_levels(self):
        """The two schema constants exist as module-level names in
        ``_schema`` (re-exported by the package) AND as ClassVars on the
        base class, and are excluded from ``asdict()`` output."""
        expected_enum = {
            "asr_backend",
            "noise_suppression_method",
            "audio_preset",
            "theme_mode",
            "theme_preset",
            "bubble_position",
            "bubble_behavior",
            "tray_left_click_action",
            "recording_mode",
        }
        assert frozenset(expected_enum) == config_schema._ENUM_FIELDS_TO_RESET_ON_LOAD
        assert "openai_api_key" in config_schema._SECRET_FIELD_NAMES_FALLBACK
        assert Config._ENUM_FIELDS_TO_RESET_ON_LOAD == config_schema._ENUM_FIELDS_TO_RESET_ON_LOAD
        assert Config._SECRET_FIELD_NAMES_FALLBACK == config_schema._SECRET_FIELD_NAMES_FALLBACK

    def test_asdict_excludes_classvars_and_transients(self):
        cfg = Config()
        data = dataclasses.asdict(cfg)
        for name in ("_mutation_lock", "_ENUM_FIELDS_TO_RESET_ON_LOAD", "_SECRET_FIELD_NAMES_FALLBACK"):
            assert name not in data
        for name in ("last_load_warnings", "_dirty", "_last_saved_bytes", "_secrets_routed_in_save"):
            assert name not in data

    def test_reset_invalid_enum_fields_impl_restores_default(self):
        """The module-level impl resets an invalid Literal value on the
        instance and appends a warning (the load-time self-heal path)."""
        cfg = Config()
        object.__setattr__(cfg, "asr_backend", "not-a-real-backend")
        config_schema._reset_invalid_enum_fields_impl(Config, cfg)
        assert cfg.asr_backend == "whisper"
        warnings = cfg.last_load_warnings or []
        assert any("asr_backend" in w for w in warnings)

    def test_secret_field_names_impl_fail_closed_source(self):
        """The impl sources the secret-field set from credential_store
        (fail-closed), matching PROVIDER_TO_CONFIG_FIELD values."""
        from voice_typer.server import credential_store

        names = config_schema._secret_field_names_impl()
        assert names == frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.values())


# ── Saving module ────────────────────────────────────────────────────────


class TestSavingModuleImpls:
    @pytest.mark.parametrize(
        "name",
        [
            "_enforce_windows_owner_only_acl",
            "_save_impl",
            "_save_strict_impl",
            "_save_unlocked_impl",
            "_save_with_mutation_lock_impl",
            "_warmup_keyring_probe_impl",
        ],
    )
    def test_save_impls_callable(self, name):
        fn = getattr(config_saving, name)
        assert callable(fn)
        # Re-exported through the package namespace for callers/tests.
        assert getattr(config_mod, name) is fn


# ── Lifecycle mixin ──────────────────────────────────────────────────────


class TestLifecycleMixinExtracted:
    @pytest.mark.parametrize(
        "name",
        [
            "__post_init__",
            "__setattr__",
            "set_mutation_lock",
            "_warmup_keyring_probe",
            "save",
            "_save_with_mutation_lock",
            "_save_unlocked",
            "_save_locked",
            "save_strict",
            "load",
            "_read_raw_json",
            "_filter_unknown_keys",
            "_run_migrations",
            "_backup_before_migration",
            "_backup_before_downgrade",
            "_coerce_streaming_fields",
            "_coerce_max_recording_time",
            "_validate_model_path",
            "_validate_qwen_model_path",
            "_validate_corrections_path",
            "_validate_privacy_consents",
            "_derive_field_type_registry",
            "_reset_invalid_enum_fields",
            "_secret_field_names",
            "_warn_and_reset",
            "_warn_and_coerce",
            "_validate_non_numeric_fields",
            "config_dir",
        ],
    )
    def test_mixin_method_surface(self, name):
        """Every lifecycle method survives the split and resolves on Config."""
        assert hasattr(config_lifecycle._ConfigLifecycleMixin, name), f"mixin missing {name!r}"
        assert hasattr(Config, name), f"Config missing {name!r}"

    def test_config_subclasses_lifecycle_mixin(self):
        assert issubclass(Config, config_lifecycle._ConfigLifecycleMixin)

    def test_save_locked_alias_still_resolves(self):
        """The pre-refactor ``_save_locked`` name remains a live alias of
        ``_save_unlocked`` (same underlying function)."""
        assert Config._save_locked is Config._save_unlocked

    def test_post_init_transient_attrs(self):
        cfg = Config()
        assert cfg.last_load_warnings is None
        assert cfg._last_saved_bytes is None
        assert cfg._dirty is True
        assert cfg._secrets_routed_in_save is False

    def test_setattr_marks_dirty_only_for_public_fields(self):
        cfg = Config()
        cfg.hotkey = "<f7>"
        assert cfg._dirty is True
        object.__setattr__(cfg, "_dirty", False)
        cfg.last_load_warnings = ["x"]  # transient — must NOT mark dirty
        assert cfg._dirty is False

    def test_set_mutation_lock_is_per_instance(self):
        lock = threading.RLock()
        a, b = Config(), Config()
        a.set_mutation_lock(lock)
        assert a._mutation_lock is lock
        assert b._mutation_lock is None
        a.set_mutation_lock(None)
        assert a._mutation_lock is None

    def test_secret_field_names_classmethod_delegates(self):
        assert Config._secret_field_names() == config_schema._secret_field_names_impl()

    def test_config_dir_property(self, tmp_config_dir):
        cfg = Config()
        assert isinstance(cfg.config_dir, Path)


# ── Delegation / monkeypatch-propagation contracts ───────────────────────


class TestDelegationContracts:
    def test_backup_before_migration_delegation_via_config_mod(self, tmp_path, monkeypatch):
        """``Config._backup_before_migration`` delegates to the extracted
        impl, which resolves the secure-io helpers via the ``config``
        module namespace — patches on ``config_mod`` must take effect."""
        calls: list[str] = []

        real_read = config_mod._secure_read_text
        real_write = config_mod._secure_atomic_write

        def spy_read(path):
            calls.append("read")
            return real_read(path)

        def spy_write(path, text, **kwargs):
            calls.append("write")
            return real_write(path, text, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_read_text", spy_read)
        monkeypatch.setattr(config_mod, "_secure_atomic_write", spy_write)

        from voice_typer.server.config_internals.migrations import _CURRENT_SCHEMA_VERSION

        config_file = tmp_path / "config.json"
        config_file.write_text('{"schema_version": 1}', encoding="utf-8")

        loaded_version = _CURRENT_SCHEMA_VERSION - 1
        Config._backup_before_migration(config_file, loaded_version)

        assert "read" in calls and "write" in calls, (
            "delegated impl must route io through the config module namespace "
            "(tests patch config_mod._secure_read_text/_secure_atomic_write)"
        )
        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert backups, "a pre-migration backup must have been written"

    def test_backup_before_downgrade_argument_order(self, tmp_path, monkeypatch):
        """The public classmethod keeps the legacy argument order
        ``(config_file, loaded_version, data)`` while the extracted impl
        takes ``(cls, data, loaded_version, config_file)`` — the
        delegator must forward positionally-corrected arguments."""
        captured: dict = {}

        def fake_impl(cls, data, loaded_version, config_file):
            captured["cls"] = cls
            captured["data"] = data
            captured["loaded_version"] = loaded_version
            captured["config_file"] = config_file

        monkeypatch.setattr(config_lifecycle, "_backup_before_downgrade_impl", fake_impl)

        cfg_file = tmp_path / "config.json"
        data = {"schema_version": 99}
        Config._backup_before_downgrade(cfg_file, 99, data)

        assert captured == {
            "cls": Config,
            "data": data,
            "loaded_version": 99,
            "config_file": cfg_file,
        }

        # And pin the impl signature itself so the order cannot silently drift.
        params = list(inspect.signature(config_lifecycle._backup_before_downgrade_impl).parameters)
        assert params == ["cls", "data", "loaded_version", "config_file"]

    def test_save_path_resolves_acl_helper_via_config_namespace(self, monkeypatch, tmp_config_dir):
        """``save``/``_save_unlocked`` call ``_enforce_windows_owner_only_acl``
        through the config module globals — patching ``config_mod.<name>``
        replaces what the save path invokes (Windows branch simulated)."""
        acl_calls: list[str] = []
        monkeypatch.setattr(config_mod, "is_windows", lambda: True)

        def spy_acl(path):
            acl_calls.append(str(path))
            return True

        monkeypatch.setattr(config_mod, "_enforce_windows_owner_only_acl", spy_acl)

        cfg = Config()
        assert cfg.save() is True
        # The dir-tightening step in save() fires before the file writes;
        # either way the helper must be reached through the patched binding.
        assert acl_calls, "save() must invoke the ACL helper via config_mod"


# ── End-to-end round trip ────────────────────────────────────────────────


class TestEndToEnd:
    def test_construct_save_reload_roundtrip(self, tmp_config_dir):
        """Construct a Config in an isolated dir, mutate a persisted
        field, save, reload from disk — the full inherited API path."""
        cfg = Config()
        cfg.hotkey = "<f9>"
        cfg.text_size = 21
        assert cfg.save() is True

        config_file = tmp_config_dir / "config.json"
        assert config_file.exists()

        reloaded = Config.load()
        assert reloaded.hotkey == "<f9>"
        assert reloaded.text_size == 21

        # A no-op resave short-circuits via the dirty flag / byte cache.
        assert reloaded.save() is True

    def test_save_strict_raises_on_failure(self, tmp_config_dir, monkeypatch):
        cfg = Config()
        monkeypatch.setattr(Config, "save", lambda self: False)
        with pytest.raises(RuntimeError, match="failed to persist config"):
            cfg.save_strict()
