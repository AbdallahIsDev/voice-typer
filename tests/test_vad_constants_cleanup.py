"""XZ-CC-1 regression: dead ``_DEFAULT_VAD_*`` constants must not come back.

The original finding flagged six duplicated VAD default constants spread
across ``vad_processor.py`` (canonical) and ``recording/recorder.py``
(compat shim). The recorder comment admitted four of the six were "no
longer referenced internally after VadProcessor extraction" — pure
dead-code duplication. The fix removed the four dead constants from
``recorder.py`` and from ``recording/__init__.py``'s public re-exports,
leaving only the two genuinely used (``_DEFAULT_VAD_SPEECH_THRESHOLD_DB``
/ ``_DEFAULT_VAD_SILENCE_THRESHOLD_DB``) which were also subsequently
removed (DT-11) in favor of importing the canonical ``DEFAULT_VAD_*``
names directly.

This test pins the removal so a future merge / refactor that
re-introduces the dead aliases (e.g. by re-running an old compat-shim
generator) is caught at test time.

Note: ``recording/__init__.py`` was historically listed as the primary
fix site for XZ-CC-1. After the Phase 4.5 package split, the actual
file is ``voice_typer/server/recording/__init__.py`` (no top-level
``recording/`` package exists in the current repo layout). This test
targets the real path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import voice_typer.server.recording as recording_pkg
from voice_typer.server import vad_processor

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECORDING_INIT = _REPO_ROOT / "voice_typer" / "server" / "recording" / "__init__.py"
_RECORDER_PY = _REPO_ROOT / "voice_typer" / "server" / "recording" / "recorder.py"

# The four dead constants flagged by
_DEAD_VAD_CONSTANTS: tuple[str, ...] = (
    "_DEFAULT_VAD_CALIBRATION_DURATION",
    "_DEFAULT_VAD_HANGOVER_FRAMES",
    "_DEFAULT_VAD_SILENCE_FRAMES",
    "_DEFAULT_VAD_SPEECH_FRAMES",
)


def _module_level_assignments(path: Path) -> set[str]:
    """Return names assigned at module level in ``path`` (top-level ``Name = ...`` only)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _imported_names(path: Path) -> set[str]:
    """Return the set of names imported via ``from X import Y`` (and aliases) in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                # Use the local alias if ``as`` was used; otherwise the
                # original name.
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


class TestDeadVadConstantsRemoved:
    """XZ-CC-1: the four dead ``_DEFAULT_VAD_*`` compat-shim constants must stay gone."""

    def test_dead_constants_not_in_recorder_module(self) -> None:
        """``recorder.py`` must NOT re-define or import the dead constants."""
        assert _RECORDER_PY.exists(), f"recorder.py not found at {_RECORDER_PY}"
        assigned = _module_level_assignments(_RECORDER_PY)
        imported = _imported_names(_RECORDER_PY)
        present = (assigned | imported) & set(_DEAD_VAD_CONSTANTS)
        assert not present, (
            "XZ-CC-1 regression: `voice_typer/server/recording/recorder.py` "
            f"re-introduces dead VAD constants {sorted(present)}. These "
            f"were removed because they duplicated the canonical "
            f"`DEFAULT_VAD_*` constants in `vad_processor.py` and had zero "
            f"internal callers after the VadProcessor extraction."
        )

    def test_dead_constants_not_in_recording_init(self) -> None:
        """``recording/__init__.py`` must NOT re-export the dead constants."""
        assert _RECORDING_INIT.exists(), f"__init__.py not found at {_RECORDING_INIT}"
        assigned = _module_level_assignments(_RECORDING_INIT)
        imported = _imported_names(_RECORDING_INIT)
        present = (assigned | imported) & set(_DEAD_VAD_CONSTANTS)
        assert not present, (
            "XZ-CC-1 regression: `voice_typer/server/recording/__init__.py` "
            f"re-exports dead VAD constants {sorted(present)}. The "
            f"public package API must not surface the compat-shim aliases."
        )

    def test_dead_constants_not_attrs_of_recording_package(self) -> None:
        """``recording.<dead_const>`` must raise ``AttributeError``."""
        for name in _DEAD_VAD_CONSTANTS:
            assert not hasattr(recording_pkg, name), (
                f"XZ-CC-1 regression: `voice_typer.server.recording.{name}` "
                f"is still accessible as a package attribute — the dead "
                f"compat-shim constant must not be re-exported."
            )

    def test_canonical_defaults_still_available_on_vad_processor(self) -> None:
        """The canonical ``DEFAULT_VAD_*`` constants must remain on ``vad_processor``."""
        for canonical in (
            "DEFAULT_VAD_CALIBRATION_DURATION",
            "DEFAULT_VAD_HANGOVER_FRAMES",
            "DEFAULT_VAD_SILENCE_FRAMES",
            "DEFAULT_VAD_SPEECH_FRAMES",
        ):
            assert hasattr(vad_processor, canonical), (
                f"XZ-CC-1 sanity check failed: canonical `{canonical}` is "
                f"missing from `vad_processor`. The dead constants were "
                f"removed on the assumption that the canonical names still "
                f"exist — if the canonicals moved, re-add an alias in the "
                f"canonical module rather than reviving the dead duplicates."
            )
