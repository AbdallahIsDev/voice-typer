"""Tests for ``Config._validate_non_numeric_fields`` clarifying docstring.

Split out of the former ``tests/test_history_and_models.py`` catch-all.
Verbatim mechanical move — same test names +
assertions, only the file location changed.
"""

from __future__ import annotations

import inspect


class TestValidateNonNumericFieldsHasClarifyingDocstring:
    """_validate_non_numeric_fields is NOT a duplicate — it's a migration layer."""

    def test_validator_has_clarifying_docstring(self):
        from voice_typer.server.config import Config

        source = inspect.getsource(Config._validate_non_numeric_fields)
        assert "migration layer" in source
