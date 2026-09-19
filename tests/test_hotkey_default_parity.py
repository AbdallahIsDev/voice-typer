"""FZ-54: cross-language parity test for the default hotkey."""

from __future__ import annotations

import re
from pathlib import Path

from voice_typer.server.config import Config

# Path to the renderer's canonical hotkey-default constant.
CONSTANTS_TS_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "components"
    / "hotkey"
    / "hotkey-format.ts"
)


def _extract_hotkey_default(ts_source: str) -> str:
    """Pull the ``HOTKEY_DEFAULT`` literal out of the TS source."""
    m = re.search(
        r"""export\s+const\s+HOTKEY_DEFAULT\s*=\s*["']([^"']+)["']\s*;""",
        ts_source,
    )
    if m is not None:
        return m.group(1)
    # Re-export form: `export { HOTKEY_DEFAULT } from "@/components/hotkey/hotkey-utils";`
    re_export = re.search(
        r"""export\s*\{\s*HOTKEY_DEFAULT\s*\}\s*from\s*["']([^"']+)["']\s*;""",
        ts_source,
    )
    assert re_export is not None, (
        'constants.ts is missing `export const HOTKEY_DEFAULT = "...";` '
        "(or `export { HOTKEY_DEFAULT } from ...`). Either the constant "
        "was renamed (update this test) or removed (update the "
        "onboarding wizard to source the default via the `get_defaults` "
        "IPC call per FZ-54)."
    )
    return re_export.group(1)


def test_constants_ts_hotkey_default_matches_config_default() -> None:
    """The TS-side ``HOTKEY_DEFAULT`` must equal ``Config().hotkey``."""
    assert CONSTANTS_TS_PATH.exists(), (
        f"constants.ts not found at {CONSTANTS_TS_PATH}, has the renderer's onboarding directory moved?"
    )
    ts_source = CONSTANTS_TS_PATH.read_text(encoding="utf-8")
    ts_value = _extract_hotkey_default(ts_source)
    # Resolve a re-export (module path) to the canonical declaration.
    if ts_value.startswith("@/"):
        # Path accepts forward slashes on every OS (including Windows),
        rel = ts_value[2:]
        renderer_src = CONSTANTS_TS_PATH.parents[3]  # …/src/renderer/src
        target = renderer_src / rel
        target = (target if target.suffix else target.with_suffix(".ts")).resolve()
        assert target.exists(), f"HOTKEY_DEFAULT re-export target not found: {target}"
        ts_value = _extract_hotkey_default(target.read_text(encoding="utf-8"))
    py_value = Config().hotkey
    assert ts_value == py_value, (
        f"constants.ts::HOTKEY_DEFAULT = {ts_value!r} but "
        f"Config().hotkey = {py_value!r}. Update constants.ts to match "
        "the Python canonical default (or refactor the renderer to fetch "
        "the default via the `get_defaults` IPC call)."
    )
