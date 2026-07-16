"""Regression test for NEW-UX-004: model delete is intentionally confirm-only.

The d-review NEW-UX-004 flagged that model delete uses a confirm dialog only,
with no undo toast (unlike History / Templates / Vocabulary which all use
``showUndoableToast`` for a 6-second undo window).

This test pins the DECISION (DOCUMENT, not IMPLEMENT): the rationale comment
must remain in ``Models.tsx`` above ``confirmDeleteModel``, and the
decision writeup must remain at ``docs/ux/model-delete-rationale.md``.

If a future contributor reverts to a confirm-only flow without the rationale
comment, OR removes the doc, this test fails — forcing them to either
re-affirm the decision (re-add the comment/doc) or implement proper undo
(and update the test accordingly).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

MODELS_TSX = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Models.tsx"

RATIONALE_DOC = REPO_ROOT / "docs" / "ux" / "model-delete-rationale.md"


class TestModelDeleteRationale:
    """NEW-UX-004: confirm-only delete is the documented, intentional choice."""

    def test_models_tsx_exists(self) -> None:
        """Guard: the source file under test must exist."""
        assert MODELS_TSX.is_file(), f"Missing Models.tsx at {MODELS_TSX}"

    def test_rationale_comment_present_in_models_tsx(self) -> None:
        """The rationale comment block must live above ``confirmDeleteModel``.

        The marker ``NEW-UX-004 (rationale): model delete is intentionally
        confirm-only`` is the unique anchor the test greps for. If a future
        refactor removes or rewords it, the test fails — forcing the author
        to either re-affirm the decision or implement undo and update this
        test.
        """
        src = MODELS_TSX.read_text(encoding="utf-8")

        # Anchor marker — must be present verbatim.
        assert "NEW-UX-004 (rationale): model delete is intentionally confirm-only" in src, (
            "Models.tsx is missing the NEW-UX-004 rationale comment above "
            "confirmDeleteModel. If you removed it on purpose, either "
            "re-add it (confirm-only is the decided UX) or implement undo "
            "and update tests/test_model_delete_ux.py."
        )

        # The comment must reference the doc so readers can find the writeup.
        assert "docs/ux/model-delete-rationale.md" in src, (
            "Models.tsx rationale comment must reference "
            "docs/ux/model-delete-rationale.md so the decision is discoverable."
        )

        # The comment must live ABOVE confirmDeleteModel (not after it) —
        # otherwise it documents nothing useful. We check the marker appears
        # before the function definition.
        marker_idx = src.find("NEW-UX-004 (rationale): model delete is intentionally confirm-only")
        fn_idx = src.find("const confirmDeleteModel")
        assert marker_idx != -1 and fn_idx != -1, "Either the rationale marker or confirmDeleteModel is missing."
        assert marker_idx < fn_idx, (
            "NEW-UX-004 rationale comment must appear BEFORE confirmDeleteModel in Models.tsx (currently it does not)."
        )

    def test_no_undo_toast_wired_for_model_delete(self) -> None:
        """The model delete flow must NOT actually wire ``showUndoableToast``.

        This is the negative half of the decision: not only must the
        rationale comment exist, the code must also actually NOT wire an
        undo toast. We assert that ``showUndoableToast`` is not imported
        and not called anywhere in ``Models.tsx``.

        We check for an *import* statement and a *call* (identifier followed
        by ``(``) rather than the bare identifier, because the rationale
        comment block itself mentions the name in prose. The point is to
        catch active wiring, not a documentation reference.

        (If a future contributor implements undo, they MUST update this
        test — that's the point.)
        """
        import re

        src = MODELS_TSX.read_text(encoding="utf-8")

        # An import of showUndoableToast would look like one of:
        #   import { showUndoableToast } from ...
        #   import {showUndoableToast} from ...
        import_pattern = re.compile(
            r"import\s*\{[^}]*\bshowUndoableToast\b[^}]*\}\s*from",
            re.MULTILINE,
        )
        assert not import_pattern.search(src), (
            "Models.tsx imports showUndoableToast — undo has been wired for "
            "model delete. Update the rationale comment and "
            "tests/test_model_delete_ux.py to reflect the new (undoable) "
            "behavior."
        )

        # A call would look like ``showUndoableToast(`` — identifier directly
        # followed by an open paren. The rationale comment uses the word in
        # prose ("... use showUndoableToast for a 6-second ...") which does
        # NOT match this pattern (no open paren after the identifier).
        call_pattern = re.compile(r"\bshowUndoableToast\s*\(", re.MULTILINE)
        assert not call_pattern.search(src), (
            "Models.tsx calls showUndoableToast(...) — undo has been wired "
            "for model delete. Update the rationale comment and "
            "tests/test_model_delete_ux.py to reflect the new (undoable) "
            "behavior."
        )

    def test_rationale_doc_exists(self) -> None:
        """The decision writeup must exist at the documented path."""
        assert RATIONALE_DOC.is_file(), (
            f"Missing rationale doc at {RATIONALE_DOC}. The NEW-UX-004 decision must be documented there."
        )

    def test_rationale_doc_mentions_decision(self) -> None:
        """The doc must actually describe the decision (not be a stub)."""
        text = RATIONALE_DOC.read_text(encoding="utf-8")
        # Must reference the task ID and the confirm-only decision.
        assert "NEW-UX-004" in text, "Rationale doc must reference the NEW-UX-004 task ID."
        assert "confirm-only" in text.lower(), "Rationale doc must state the confirm-only decision."
        # Must explain WHY undo is bad — both bad options.
        assert "soft-delete" in text.lower() or "soft delete" in text.lower(), (
            "Rationale doc must explain why soft-delete undo is rejected."
        )
        assert "re-download" in text.lower() or "redownload" in text.lower(), (
            "Rationale doc must explain why re-download-as-undo is rejected."
        )

    def test_backend_delete_is_hard_delete(self) -> None:
        """The backend ``delete_model`` must remain a hard ``shutil.rmtree``.

        This pins the assumption behind the DOCUMENT decision: model delete
        is a real on-disk delete, not a soft-delete to a trash dir. If a
        future change makes it soft-delete, the rationale (and this test)
        must be revisited.
        """
        service_py = REPO_ROOT / "voice_typer" / "server" / "service.py"
        assert service_py.is_file(), "voice_typer/server/service.py missing"
        src = service_py.read_text(encoding="utf-8")

        # Locate the delete_model method body.
        fn_idx = src.find("def delete_model(")
        assert fn_idx != -1, "VoiceTyperService.delete_model not found"
        # Take the next ~120 lines (the method is ~80 lines per the source).
        method_body = src[fn_idx : fn_idx + 4000]

        assert "shutil.rmtree" in method_body, (
            "delete_model must use shutil.rmtree (hard delete). If you "
            "changed it to a soft-delete / trash-dir move, update "
            "docs/ux/model-delete-rationale.md and "
            "tests/test_model_delete_ux.py — undo may now be cheap."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
