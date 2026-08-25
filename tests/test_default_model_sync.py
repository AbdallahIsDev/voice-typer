"""Cross-language parity test for the default model size.

The default model is defined ONCE in the Python backend as
``voice_typer.server.model_registry.DEFAULT_MODEL_SIZE`` (the single
source of truth — changing the default is a one-line change there).
The renderer's onboarding wizard has a TS-side copy at
``voice_typer/client/src/renderer/src/pages/onboarding/lib/constants.ts::MODEL_DEFAULT``.

The two are independent — the TS file ships in the client bundle and
cannot import the Python constant at runtime. Drift between the two
would make the onboarding wizard pre-select a different model than the
backend's config default / load-time coercion reset target, so a user
who accepts the default would see one model in the wizard and get
another (or a config-reset warning) from the backend.

This test reads the TS file's source text and asserts that the
``MODEL_DEFAULT`` literal matches ``DEFAULT_MODEL_SIZE``. It is a
static source-level check (no Vite/TS runtime needed) so it runs in
the standard pytest collection. Mirrors the HOTKEY_DEFAULT parity test
in ``tests/test_hotkey_default_parity.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

# Path to the renderer's onboarding constants module.
CONSTANTS_TS_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "pages"
    / "onboarding"
    / "lib"
    / "constants.ts"
)


def _extract_model_default(ts_source: str) -> str:
    """Pull the ``MODEL_DEFAULT`` literal out of the TS source.

    The constant is declared as ``export const MODEL_DEFAULT = "...";``
    on a single line. The match is intentionally strict about the
    leading ``export`` / ``const`` tokens so a renamed constant does
    not silently pass.
    """
    m = re.search(
        r"""export\s+const\s+MODEL_DEFAULT\s*=\s*["']([^"']+)["']\s*;""",
        ts_source,
    )
    assert m is not None, (
        'constants.ts is missing `export const MODEL_DEFAULT = "...";`. '
        "Either the constant was renamed (update this test) or removed "
        "(update the onboarding wizard to source the default elsewhere)."
    )
    return m.group(1)


def test_constants_ts_model_default_matches_backend_default() -> None:
    """The TS-side ``MODEL_DEFAULT`` must equal ``DEFAULT_MODEL_SIZE``."""
    assert CONSTANTS_TS_PATH.exists(), (
        f"constants.ts not found at {CONSTANTS_TS_PATH} — has the renderer's onboarding directory moved?"
    )
    ts_source = CONSTANTS_TS_PATH.read_text(encoding="utf-8")
    ts_value = _extract_model_default(ts_source)
    assert ts_value == DEFAULT_MODEL_SIZE, (
        f"constants.ts::MODEL_DEFAULT = {ts_value!r} but "
        f"model_registry.DEFAULT_MODEL_SIZE = {DEFAULT_MODEL_SIZE!r}. "
        "Update constants.ts to match the backend's canonical default "
        "(one-line change in `voice_typer/server/model_registry.py`)."
    )
