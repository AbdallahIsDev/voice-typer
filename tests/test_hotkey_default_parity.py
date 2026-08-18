"""FZ-54: cross-language parity test for the default hotkey.

The default hotkey ``<caps_lock>`` is defined ONCE in the Python
backend as ``voice_typer.server.config.DEFAULT_HOTKEY`` and exposed
via ``Config().hotkey`` (which calls ``_default_hotkey_for_platform``).
The renderer's onboarding wizard has a TS-side copy at
``voice_typer/client/src/renderer/src/components/hotkey/hotkey-format.ts::HOTKEY_DEFAULT``.

The two are independent — the TS file ships in the client bundle and
cannot import the Python constant at runtime. Drift between the two
would silently make the onboarding wizard pre-select a different
hotkey than the backend will register, so a user who accepts the
default would have the wizard show one hotkey and the global hotkey
dispatcher register another.

This test reads the TS file's source text and asserts that the
``HOTKEY_DEFAULT`` literal value matches ``Config().hotkey``. It is a
static source-level check (no Vite/TS runtime needed) so it runs in
the standard pytest collection.
"""

from __future__ import annotations

import re
from pathlib import Path

from voice_typer.server.config import Config

# Path to the renderer's canonical hotkey-default constant.
# ``HOTKEY_DEFAULT`` was consolidated into ``hotkey-format.ts`` (the
# onboarding ``constants.ts`` now re-exports it via ``export *``, which
# the regex below cannot follow) — point the resolver directly at the
# canonical declaration so the parity check stays exact.
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
    """Pull the ``HOTKEY_DEFAULT`` literal out of the TS source.

    The constant is declared as ``export const HOTKEY_DEFAULT = "...";``
    on a single line (or re-exported from another module as
    ``export { HOTKEY_DEFAULT } from "...";``). We tolerate surrounding
    whitespace and the optional ``_L10N`` suffix used by future locale
    variants, but the match is intentionally strict about the leading
    ``export`` / ``const`` tokens so a renamed constant does not
    silently pass.

    The re-export form is resolved by returning the re-export target
    (a module path); the caller then reads that file and extracts the
    literal from the canonical declaration.
    """
    m = re.search(
        r"""export\s+const\s+HOTKEY_DEFAULT\s*=\s*["']([^"']+)["']\s*;""",
        ts_source,
    )
    if m is not None:
        return m.group(1)
    # Re-export form: `export { HOTKEY_DEFAULT } from "@/components/hotkey/hotkey-utils";`
    # (the constant moved to hotkey-utils.ts in the hotkey-module
    # consolidation; constants.ts re-exports it so existing importers
    # and the lockstep history stay intact). Resolve it so a re-export
    # move cannot silently pass a stale value.
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
    """The TS-side ``HOTKEY_DEFAULT`` must equal ``Config().hotkey``.

    FZ-54: the canonical default lives in
    ``voice_typer.server.config.DEFAULT_HOTKEY`` /
    ``_default_hotkey_for_platform``. The renderer copy MUST match.
    """
    assert CONSTANTS_TS_PATH.exists(), (
        f"constants.ts not found at {CONSTANTS_TS_PATH} — has the renderer's onboarding directory moved?"
    )
    ts_source = CONSTANTS_TS_PATH.read_text(encoding="utf-8")
    ts_value = _extract_hotkey_default(ts_source)
    # Resolve a re-export (module path) to the canonical declaration.
    # The `@/` alias maps to the renderer source root
    # (src/renderer/src), not the re-exporting file's directory.
    if ts_value.startswith("@/"):
        # Path accepts forward slashes on every OS (including Windows),
        # so no separator rewriting — a literal backslash here breaks
        # the lookup on POSIX (backslash is a valid filename char).
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
