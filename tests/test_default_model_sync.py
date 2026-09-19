"""Cross-language parity test for the default model size."""

from __future__ import annotations

import re
from pathlib import Path

from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

# Canonical home of the renderer's MODEL_DEFAULT (lib layer).
MODEL_DEFAULT_TS_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "lib"
    / "utils"
    / "models.ts"
)

# Compatibility re-export in the onboarding constants module.
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
    """Pull the ``MODEL_DEFAULT`` literal out of the TS source."""
    m = re.search(
        r"""export\s+const\s+MODEL_DEFAULT\s*=\s*["']([^"']*)["']\s*;""",
        ts_source,
    )
    assert m is not None, (
        'models.ts is missing `export const MODEL_DEFAULT = "...";`. '
        "Either the constant was renamed (update this test) or removed "
        "(update the wizard to source the default elsewhere)."
    )
    return m.group(1)


def test_constants_ts_model_default_matches_backend_default() -> None:
    """The TS-side ``MODEL_DEFAULT`` must equal ``DEFAULT_MODEL_SIZE``."""
    assert MODEL_DEFAULT_TS_PATH.exists(), (
        f"models.ts not found at {MODEL_DEFAULT_TS_PATH}, has the renderer's lib/utils directory moved?"
    )
    ts_source = MODEL_DEFAULT_TS_PATH.read_text(encoding="utf-8")
    ts_value = _extract_model_default(ts_source)
    assert ts_value == DEFAULT_MODEL_SIZE, (
        f"models.ts::MODEL_DEFAULT = {ts_value!r} but "
        f"model_registry.DEFAULT_MODEL_SIZE = {DEFAULT_MODEL_SIZE!r}. "
        "Update models.ts to match the backend's canonical default "
        "(one-line change in `voice_typer/server/model_registry.py`)."
    )

    onboarding_source = CONSTANTS_TS_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"""export\s*\{\s*MODEL_DEFAULT\s*\}\s*from\s*["']@/lib/utils/models["']""",
        onboarding_source,
    ), (
        "constants.ts must keep the `export { MODEL_DEFAULT } from "
        "@/lib/utils/models` compatibility re-export - legacy onboarding "
        "importers resolve through it."
    )
