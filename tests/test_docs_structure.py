"""Docs existence + content-contract smoke tests."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

GDPR_EXPORT_DOC = REPO_ROOT / "docs" / "privacy" / "gdpr-export.md"
GDPR_DELETE_DOC = REPO_ROOT / "docs" / "privacy" / "gdpr-delete.md"

# The pre-split ``text_cleanup.py`` is now a package; the pinned regex
TEXT_CLEANUP_PKG_DIR = REPO_ROOT / "voice_typer" / "server" / "text_cleanup"


def _text_cleanup_sources() -> str:
    return "\n".join(sorted(p.read_text(encoding="utf-8") for p in TEXT_CLEANUP_PKG_DIR.glob("*.py")))


class TestGdprDocsExist:
    """GDPR export/delete feature-gap docs exist."""

    def test_gdpr_export_doc_exists(self):
        assert GDPR_EXPORT_DOC.exists(), f"Expected GDPR export feature-gap doc at {GDPR_EXPORT_DOC}"

    def test_gdpr_delete_doc_exists(self):
        assert GDPR_DELETE_DOC.exists(), f"Expected GDPR delete feature-gap doc at {GDPR_DELETE_DOC}"

    def test_gdpr_export_doc_mentions_article_20(self):
        text = GDPR_EXPORT_DOC.read_text(encoding="utf-8")
        # Must reference GDPR Article 20 (right to portability).
        assert "Article 20" in text, "gdpr-export.md must reference GDPR Article 20 (portability)"

    def test_gdpr_delete_doc_mentions_article_17(self):
        text = GDPR_DELETE_DOC.read_text(encoding="utf-8")
        # Must reference GDPR Article 17 (right to erasure).
        assert "Article 17" in text, "gdpr-delete.md must reference GDPR Article 17 (erasure)"


class TestPunctuationCheatSheetSourceOfTruth:
    """
    Cheat sheet content is pinned to text_cleanup.py's
    This Python test pins the SOURCE-OF-TRUTH regex in text_cleanup.py
    """

    def test_text_cleanup_punct_regex_still_recognizes_canonical_six(self):
        """``_RE_SPACING_PUNCT_BEFORE`` in text_cleanup.py:374 must"""
        source = _text_cleanup_sources()
        # The regex character class is `[,.;:!?]`.
        assert r"re.compile(r'\s+([,.;:!?])')" in source or (r're.compile(r"\s+([,.;:!?])")' in source), (
            "text_cleanup.py must still define "
            "_RE_SPACING_PUNCT_BEFORE = re.compile(r'\\s+([,.;:!?])') "
            "— the cheat sheet's source of truth"
        )

    def test_grep_no_spurious_legacy_punct_word_map(self):
        """Sanity: text_cleanup.py does NOT contain a 'spoken word →"""
        source = _text_cleanup_sources()
        assert "SPOKEN_PUNCT" not in source
        assert "PUNCT_WORD_MAP" not in source


class TestDocsADirectory:
    """Verify docs/adr directory exists with template and first ADR."""

    def test_adr_directory_exists(self):
        """docs/adr/ directory should exist."""
        from pathlib import Path

        adr_dir = Path(__file__).resolve().parent.parent / "docs" / "adr"
        assert adr_dir.exists(), "docs/adr/ directory should exist"

    def test_template_exists(self):
        """docs/adr/template.md should exist."""
        from pathlib import Path

        template = Path(__file__).resolve().parent.parent / "docs" / "adr" / "template.md"
        assert template.exists(), "ADR template should exist"

    def test_first_adr_exists(self):
        """docs/adr/0001-record-architecture-decisions.md should exist."""
        from pathlib import Path

        adr = Path(__file__).resolve().parent.parent / "docs" / "adr" / "0001-record-architecture-decisions.md"
        assert adr.exists(), "First ADR should exist"


class TestAPIDocs:
    """Verify public API documentation exists."""

    def test_api_docs_exist(self):
        """docs/API.md should exist."""
        from pathlib import Path

        api_doc = Path(__file__).resolve().parent.parent / "docs" / "API.md"
        assert api_doc.exists(), "API documentation should exist"

    def test_api_docs_mention_key_classes(self):
        """API docs should document VoiceTyperApp, Recorder, Config."""
        from pathlib import Path

        api_doc = Path(__file__).resolve().parent.parent / "docs" / "API.md"
        content = api_doc.read_text()
        for keyword in ["VoiceTyperApp", "Recorder", "Config", "ClipboardManager", "IPC"]:
            assert keyword in content, f"API docs should mention {keyword}"
