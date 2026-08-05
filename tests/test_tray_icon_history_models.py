"""Regression tests for ``tray_icon._make_icon`` source content.

Split out of the former ``tests/test_history_and_models.py`` catch-all
(Phase 4.5 / TC-15). Verbatim mechanical move — same test names +
assertions, only the file location changed.

TODO: merge into the canonical test_<module>.py file in a future session.
"""

from __future__ import annotations

import inspect


class TestTrayIconNoLongerReferencesStaleSvg:
    """vt_logo.svg references updated."""

    def test_tray_icon_no_longer_references_vt_logo(self):
        from voice_typer.server import tray_icon

        source = inspect.getsource(tray_icon._make_icon)
        assert "from vt_logo.svg" not in source


class TestTrayIconUsesGetchannelNotSplitIndex:
    """Use getchannel('A') instead of split()[3]."""

    def test_no_split_index_3(self):
        from voice_typer.server import tray_icon

        source = inspect.getsource(tray_icon._make_icon)
        code_lines = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        assert "split()[3]" not in code_only
        # The production source uses ``getchannel("A")`` (double quotes);
        # accept either quote style so the test is resilient to the
        # formatter's preference.
        assert ('getchannel("A")' in code_only) or ("getchannel('A')" in code_only), (
            "expected getchannel('A') or getchannel(\"A\") in _make_icon source"
        )
