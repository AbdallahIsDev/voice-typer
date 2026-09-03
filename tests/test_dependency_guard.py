"""Tests for the startup dependency guard.

The guard's contract: every entry in ``REQUIRED_IMPORTS`` is importable
in a healthy environment (exit 0, no output); a missing entry is
reported on stderr with the exact repair command and a non-zero exit.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from voice_typer.server import dependency_guard


class TestHealthyEnvironment:
    def test_all_required_imports_present_in_this_env(self):
        """The dev venv must satisfy the guard — a new REQUIRED_IMPORTS
        entry that this environment lacks fails here, forcing a decision
        (install it or argue it out of the list)."""
        missing = dependency_guard.missing_required()
        assert missing == [], f"REQUIRED_IMPORTS has entries this environment lacks: {missing}"

    def test_run_guard_returns_zero_when_healthy(self):
        assert dependency_guard.run_guard() == 0


class TestMissingDependency:
    @pytest.fixture()
    def fake_import_error(self):
        """Make ``import cryptography`` raise while everything else passes."""
        real = importlib.import_module

        def fake(name, *args, **kwargs):
            if name == "cryptography":
                raise ImportError("No module named 'cryptography'")
            return real(name, *args, **kwargs)

        with patch.object(importlib, "import_module", side_effect=fake):
            yield

    def test_missing_import_is_reported(self, fake_import_error, capsys):
        rc = dependency_guard.run_guard()
        assert rc == 1
        err = capsys.readouterr().err
        assert "[DEPS]" in err
        assert "cryptography" in err
        # The report names the consequence so the operator knows what degrades.
        assert "key-unavailable" in err
        # The report carries an actionable repair command with THIS interpreter.
        assert "-m pip install" in err

    def test_all_missing_reported_together(self, fake_import_error):
        """One missing dep must not mask the others: every failing entry
        appears in a single report."""
        missing = dependency_guard.missing_required()
        assert [m[0] for m in missing] == ["cryptography"]

    def test_partial_failure_leaves_other_checks_real(self, fake_import_error):
        """Only the patched import fails — sounddevice/keyring/etc. are
        genuinely importable here and must NOT be reported missing."""
        missing = dependency_guard.missing_required()
        assert all(name != "sounddevice" for name, _ in missing)
        assert all(name != "keyring" for name, _ in missing)


class TestListIntegrity:
    def test_required_imports_shape(self):
        assert dependency_guard.REQUIRED_IMPORTS
        for name, consequence in dependency_guard.REQUIRED_IMPORTS:
            assert isinstance(name, str) and name
            assert isinstance(consequence, str) and consequence

    def test_cryptography_is_guarded(self):
        """The guard exists because cryptography went missing twice and
        the failure masqueraded as key loss — pin its presence."""
        names = [name for name, _ in dependency_guard.REQUIRED_IMPORTS]
        assert "cryptography" in names
