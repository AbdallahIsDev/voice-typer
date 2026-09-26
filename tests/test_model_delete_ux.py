"""Regression test for model delete is intentionally confirm-only."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

MODELS_TSX = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Models.tsx"

RATIONALE_DOC = REPO_ROOT / "docs" / "ux" / "model-delete-rationale.md"


class TestModelDeleteRationale:
    """confirm-only delete is the documented, intentional choice."""

    def test_models_tsx_exists(self) -> None:
        """Guard: the source file under test must exist."""
        assert MODELS_TSX.is_file(), f"Missing Models.tsx at {MODELS_TSX}"

    def test_rationale_comment_present_in_models_tsx(self) -> None:
        """The rationale comment block must live above the delete-confirm UI."""
        src = MODELS_TSX.read_text(encoding="utf-8")

        # Anchor marker, must be present verbatim.
        assert "(rationale): model delete is intentionally confirm-only" in src, (
            "Models.tsx is missing the rationale comment above "
            "the ConfirmDialog for model delete. If you removed it on "
            "purpose, either re-add it (confirm-only is the decided UX) or "
            "implement undo and update tests/test_model_delete_ux.py."
        )

        # The comment must reference the doc so readers can find the writeup.
        assert "docs/ux/model-delete-rationale.md" in src, (
            "Models.tsx rationale comment must reference "
            "docs/ux/model-delete-rationale.md so the decision is discoverable."
        )

        # The comment must live ABOVE the ConfirmDialog JSX (not after it) —
        marker_idx = src.find("(rationale): model delete is intentionally confirm-only")
        fn_idx = src.find("<ConfirmDialog")
        assert marker_idx != -1 and fn_idx != -1, "Either the rationale marker or the ConfirmDialog JSX is missing."
        assert marker_idx < fn_idx, (
            "rationale comment must appear BEFORE the ConfirmDialog JSX in Models.tsx (currently it does not)."
        )

    def test_no_undo_toast_wired_for_model_delete(self) -> None:
        """The model delete flow must NOT actually wire ``showUndoableToast``."""
        import re

        src = MODELS_TSX.read_text(encoding="utf-8")

        # An import of showUndoableToast would look like one of:
        import_pattern = re.compile(
            r"import\s*\{[^}]*\bshowUndoableToast\b[^}]*\}\s*from",
            re.MULTILINE,
        )
        assert not import_pattern.search(src), (
            "Models.tsx imports showUndoableToast, undo has been wired for "
            "model delete. Update the rationale comment and "
            "tests/test_model_delete_ux.py to reflect the new (undoable) "
            "behavior."
        )

        # A call would look like ``showUndoableToast(``, identifier directly
        call_pattern = re.compile(r"\bshowUndoableToast\s*\(", re.MULTILINE)
        assert not call_pattern.search(src), (
            "Models.tsx calls showUndoableToast(...), undo has been wired "
            "for model delete. Update the rationale comment and "
            "tests/test_model_delete_ux.py to reflect the new (undoable) "
            "behavior."
        )

    def test_rationale_doc_exists(self) -> None:
        """The decision writeup must exist at the documented path."""
        assert RATIONALE_DOC.is_file(), (
            f"Missing rationale doc at {RATIONALE_DOC}. The decision must be documented there."
        )

    def test_rationale_doc_mentions_decision(self) -> None:
        """The doc must actually describe the decision (not be a stub)."""
        text = RATIONALE_DOC.read_text(encoding="utf-8")
        # Must reference the confirm-only decision.
        assert "confirm-only" in text.lower(), "Rationale doc must state the confirm-only decision."
        # Must explain WHY undo is bad, both bad options.
        assert "soft-delete" in text.lower() or "soft delete" in text.lower(), (
            "Rationale doc must explain why soft-delete undo is rejected."
        )
        assert "re-download" in text.lower() or "redownload" in text.lower(), (
            "Rationale doc must explain why re-download-as-undo is rejected."
        )

    def test_backend_delete_is_hard_delete(self) -> None:
        """
        The backend ``delete_model`` must remain a hard ``shutil.rmtree``.
        This pins the assumption behind the DOCUMENT decision: model delete
        """
        service_py = REPO_ROOT / "voice_typer" / "server" / "service" / "model"
        if service_py.is_dir():
            src = "".join(p.read_text(encoding="utf-8") for p in sorted(service_py.glob("*.py")))
        else:
            src = service_py.with_suffix(".py").read_text(encoding="utf-8")
        assert src, "service/model source not found"

        # Locate the delete_model method body.
        fn_idx = src.find("def delete_model(")
        assert fn_idx != -1, "LausuService.delete_model not found"
        next_def = src.find("\n    def ", fn_idx + 1)
        method_body = src[fn_idx : next_def if next_def != -1 else fn_idx + 4000]

        assert "shutil.rmtree" in method_body, (
            "delete_model must use shutil.rmtree (hard delete). If you "
            "changed it to a soft-delete / trash-dir move, update "
            "docs/ux/model-delete-rationale.md and "
            "tests/test_model_delete_ux.py, undo may now be cheap."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
