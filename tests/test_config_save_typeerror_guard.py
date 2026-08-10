"""``Config.save()`` must catch ``TypeError`` / ``ValueError``
from ``json.dumps`` and return ``False`` (not propagate).

The pre-fix ``save()`` had this exception tuple::

    except (TimeoutError, OSError, PermissionError) as e:
        log.error("[CONFIG] Failed to save config: %s", e)
        return False

``json.dumps`` (called inside ``_save_unlocked`` via ``asdict(self)``)
can raise:

* ``TypeError`` when a field holds a non-JSON-serializable value (e.g.
  a ``set`` / ``datetime`` / custom object smuggled in via
  ``setattr`` or a botched migration).
* ``ValueError`` for circular references (rare but possible if a
  custom ``__repr__`` / ``__str__`` triggers it during dumps).

The pre-fix tuple did NOT include these — the exception propagated to
the caller, violating the ``save()`` docstring's "never raises"
contract (which the IPC ``set_config`` path relies on: a ``TypeError``
would crash the IPC handler thread instead of returning a ``False``
ack the renderer can surface as a save-failed toast).

The fix widens the tuple to ``(TimeoutError, OSError, PermissionError,
TypeError, ValueError)`` and logs at ERROR so the operator can
diagnose which field is non-serializable.

Platform note: validated ON LINUX (sandbox). The serialization
behavior is platform-agnostic (pure-Python ``json.dumps``).
"""

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
        """A non-JSON-serializable value in a Config field (e.g. a
        ``set`` smuggled in via ``setattr``) must cause ``save()`` to
        return ``False`` — NOT raise ``TypeError``. The error must be
        logged at ERROR so the operator can diagnose the bad field.
        """
        # Write an initial config so the backup branch has something
        # to read (the failure happens AFTER the backup block, inside
        # ``json.dumps(asdict(self))``).
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()

        # Inject a non-serializable value via setattr. ``asdict(self)``
        # picks this up because the field IS in the dataclass
        # ``__dict__`` (even though it's not a declared dataclass
        # field — ``asdict`` returns the declared fields only; we need
        # to override a declared field's value with a non-serializable
        # one).
        #
        # ``disabled_backends: list[str]`` is a declared field. Replace
        # its value with a ``set`` (non-JSON-serializable —
        # ``json.dumps`` raises ``TypeError: Object of type set is not
        # JSON serializable``).
        cfg.disabled_backends = {"whisper", "qwen"}  # type: ignore[assignment]  # noqa: E501 — intentional bad type for the test

        # The save must NOT raise —  widens save()'s except
        # tuple to catch TypeError.
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
        """contract: ``save()`` must NEVER raise ``TypeError``
        to the caller — it must catch it and return ``False``. The
        IPC ``set_config`` path relies on this "never raises"
        contract: a propagated ``TypeError`` would crash the IPC
        handler thread instead of returning a ``False`` ack the
        renderer can surface as a save-failed toast.
        """
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()

        # Smuggle in a non-serializable value (a custom object with no
        # JSON representation).
        class _NotJsonSerializable:
            pass

        cfg.disabled_backends = [_NotJsonSerializable()]  # type: ignore[list-item]

        # Must not raise — TypeError is caught by save()'s widened
        # except tuple.
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
        """``json.dumps`` can also raise ``ValueError`` for
        circular references (when a deeply-nested structure repeats
        itself). ``save()`` must catch ``ValueError`` too and return
        ``False``.

        We simulate this by making ``disabled_backends`` a list whose
        ``__repr__`` is fine but whose contents cause ``json.dumps``
        to raise. The simplest repro is a custom class whose
        ``__iter__`` yields itself (infinite loop caught by
        ``json.dumps`` as ``ValueError``). We use a simpler approach:
        patch ``json.dumps`` directly to raise ``ValueError`` so we
        deterministically exercise the ValueError branch.

        Note : ``Config._save_unlocked`` short-circuits at the
        top when ``_dirty is False`` AND ``_last_saved_bytes`` is
        populated (skipping ``asdict`` + ``json.dumps`` entirely). The
        post-migration save inside ``Config.load()`` populates both, so
        a bare ``cfg = Config.load(); cfg.save()`` would short-circuit
        and never reach ``json.dumps``. Mutating a field (``cfg.hotkey =
        "<f5>"``) sets ``_dirty = True`` via the ``__setattr__``
        override, ensuring ``json.dumps`` is reached so the patch takes
        effect. This mirrors the pattern in
        ``test_save_happy_path_still_returns_true`` below.
        """
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()
        # mutate a field so _dirty=True and the dirty-flag
        # short-circuit at the top of _save_unlocked does NOT skip
        # the json.dumps call (which is patched below).
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
        """Sanity: a normal save with all-serializable fields must
        still return True. Guards against an over-correction that
        always returns False."""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()
        cfg.hotkey = "<f5>"  # change something so save has work to do
        result = cfg.save()

        assert result is True, "over-correction: a normal save with all-serializable fields should return True."
        # The new hotkey must be persisted.
        new_data = json.loads(config_file.read_text())
        assert new_data["hotkey"] == "<f5>"
