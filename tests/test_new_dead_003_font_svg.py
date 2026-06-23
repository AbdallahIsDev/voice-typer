"""Regression tests for NEW-DEAD-003: dead font + SVG references in
pyproject.toml.

The ``pyproject.toml`` previously declared
``"voice_typer.server" = ["assets/fonts/hgi-stroke-rounded.ttf"]`` as
package-data, but:
- The file (1.9 MB) was never loaded by any Python source file.
- The new Electron app uses ``@hugeicons/react`` from npm instead.
- The ``assets/icons/`` directory referenced 19 SVG files that were
  also unused.

The fix removes the package-data reference, saving 1.9 MB in the
built wheel and installer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


class TestPyprojectNoDeadFontReference:
    """NEW-DEAD-003: pyproject.toml must not reference the dead font."""

    def test_no_hgi_font_reference(self):
        """The 1.9 MB font file must not be referenced as package-data."""
        content = PYPROJECT.read_text()
        assert "hgi-stroke-rounded" not in content, (
            "pyproject.toml still references the dead hgi-stroke-rounded.ttf "
            "font (1.9 MB, never loaded by any Python source file)"
        )

    def test_no_package_data_section_for_fonts(self):
        """The ``[tool.setuptools.package-data]`` section must not
        reference fonts or icons."""
        content = PYPROJECT.read_text()
        # The section may exist for other purposes, but it must not
        # reference the dead font/icons.
        if "[tool.setuptools.package-data]" in content:
            # Find the section and check its contents.
            start = content.index("[tool.setuptools.package-data]")
            # Find the next section header.
            end = len(content)
            for marker in ("\n[",):
                idx = content.find(marker, start + 1)
                if idx != -1:
                    end = min(end, idx)
            section = content[start:end]
            assert "hgi" not in section.lower(), (
                f"package-data section still references hgi font: {section}"
            )
            assert "fonts/" not in section, (
                f"package-data section still references fonts/ directory: {section}"
            )
            assert "icons/" not in section, (
                f"package-data section still references icons/ directory: {section}"
            )


class TestNoPythonCodeLoadsTheFont:
    """Sanity check: no Python source file should reference the font
    (the issue said zero source files load it)."""

    def test_no_python_imports_hgi_font(self):
        """No Python file in voice_typer/ should reference the font."""
        root = Path(__file__).resolve().parent.parent / "voice_typer"
        offenders = []
        for py_file in root.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "hgi-stroke-rounded" in content or "hgi_stroke" in content:
                offenders.append(str(py_file))
        assert not offenders, (
            f"Python files still reference the dead font: {offenders}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
