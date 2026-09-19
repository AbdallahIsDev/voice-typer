"""``Config.save()`` must catch ``TypeError`` / ``ValueError``"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from voice_typer.server.config import Config


@pytest.fixture
def _isolated_config_dir(tmp_config_dir: Path) -> Path:
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield tmp_config_dir


class TestSaveCatchesJsonDumpsTypeError:
    """``save()`` must catch ``TypeError`` from ``json.dumps``."""

    def test_save_returns_false_on_non_serializable_field(
        self,
        _isolated_config_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """return ``False``. NOT raise ``TypeError``. The error must be"""
        # Write an initial config so the backup branch has something
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()

        # Inject a non-serializable value via setattr. ``asdict(self)``
        cfg.disabled_backends = {"whisper", "qwen"}  # type: ignore[assignment]  # noqa: E501, intentional bad type for the test

        # The save must NOT raise, widens save()'s except
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.config"):
            result = cfg.save()

        assert result is False, (
            "regression: save() should return False when "
            "json.dumps raises TypeError, but it returned True (the "
            "non-serializable field was silently dropped or the "
            "exception was swallowed elsewhere)."
        )

        # The ERROR log must mention the serialization failure.
        error_records = [
            r
            for r in caplog.records
            if r.name == "voice_typer.server.config"
            and r.levelno >= logging.ERROR
            and ("serialize" in r.message.lower() or "Failed to serialize" in r.message)
        ]
        assert len(error_records) >= 1, (
            f"expected an ERROR log about serialization failure, got records: {[r.message for r in caplog.records]}"
        )

    def test_save_does_not_propagate_typeerror(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """contract: ``save()`` must NEVER raise ``TypeError``"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()

        # Smuggle in a non-serializable value (a custom object with no
        class _NotJsonSerializable:
            pass

        cfg.disabled_backends = [_NotJsonSerializable()]  # type: ignore[list-item]

        # Must not raise, TypeError is caught by save()'s widened
        try:
            result = cfg.save()
        except TypeError as exc:
            pytest.fail(
                "regression: save() propagated TypeError to "
                f"the caller: {exc!r}. The save() except tuple must "
                "catch TypeError and return False."
            )
        assert result is False

    def test_save_returns_false_on_circular_reference(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """circular references (when a deeply-nested structure repeats"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()
        cfg.hotkey = "<f5>"

        # Patch json.dumps in the config module to raise ValueError.
        import voice_typer.server.config as config_mod

        original_dumps = config_mod.json.dumps

        def _raise_value_error(*args, **kwargs):
            raise ValueError("simulated circular reference")

        config_mod.json.dumps = _raise_value_error  # type: ignore[method-assign]
        try:
            result = cfg.save()
        finally:
            config_mod.json.dumps = original_dumps  # type: ignore[method-assign]

        assert result is False, (
            "regression: save() should return False when json.dumps raises ValueError, but it returned True."
        )

    def test_save_happy_path_still_returns_true(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """Sanity: a normal save with all-serializable fields must"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()
        cfg.hotkey = "<f5>"  # change something so save has work to do
        result = cfg.save()

        assert result is True, "over-correction: a normal save with all-serializable fields should return True."
        # The new hotkey must be persisted.
        new_data = json.loads(config_file.read_text())
        assert new_data["hotkey"] == "<f5>"
