"""Regression tests for the three High findings:"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.fixtures.config_helpers import patch_config_dir_refs  # noqa: E402


def _bundled_corrections(tmp_path: Path) -> Path:
    """Write a minimal bundled corrections.json so VocabularyManager"""
    data = {
        "misspellings": {"teh": "the", "recieve": "receive"},
        "phrase_corrections": [["voice to 2 text", "voice to text"]],
        "extra_word_patterns": [["without whether", "whether"]],
        "technical_terms": {"pyathon": "python"},
        "names": {},
        "products": {},
    }
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _make_service(tmp_path: Path):
    """Build a ``LausuService`` against a tmp config dir."""
    from voice_typer.server import config as cfg_mod
    from voice_typer.server.service import LausuService
    from voice_typer.server.templates import TemplateManager
    from voice_typer.server.vocabulary import VocabularyManager

    mp = pytest.MonkeyPatch()
    mp.setattr(cfg_mod, "_config_dir", lambda: tmp_path)

    bundled = _bundled_corrections(tmp_path)
    vm = VocabularyManager(config_dir=tmp_path, bundled_path=bundled)
    tm = TemplateManager(config_dir=tmp_path)

    class _StubApp:
        config = type("C", (), {"config_dir": tmp_path})()
        tray = MagicMock()
        history_db = None
        _llm_polisher = None
        _cloud_engine = None

        def __init__(self):
            # Bypass the @property setter on LausuApp: this stub
            self._vocabulary_manager = vm
            self._template_manager = tm

    app = _StubApp()
    svc = LausuService(app)
    return svc, app, vm, tm, mp


class TestGetVocabularyReusesLiveManager:
    """IN-31: ``get_vocabulary`` must reuse the live"""

    def test_get_vocabulary_uses_live_vocabulary_manager(self, tmp_path):
        """The returned data must reflect mutations made to the LIVE"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            # Mutate the LIVE manager.
            assert vm.add_entry("technical_terms", "liveonly", "livevalue") is True
            data = svc.get_vocabulary()
            tech = data.get("technical_terms", {})
            assert tech.get("liveonly") == "livevalue", (
                "get_vocabulary did not observe the live VocabularyManager mutation, "
                "it likely constructed a throwaway instance that read the pre-mutation "
                "state from disk (IN-31 regression)."
            )
        finally:
            mp.undo()

    def test_get_vocabulary_returns_deep_copy_dict_category(self, tmp_path):
        """Mutating a returned DICT category (e.g. ``misspellings``)"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            data = svc.get_vocabulary()
            # Mutate the returned dict category.
            data["misspellings"]["teh"] = "MUTATED_BY_RENDERER"
            data["misspellings"]["renderer_only"] = "added"
            # The live manager's data must be UNCHANGED.
            live = vm.get_all()
            assert live["misspellings"]["teh"] == "the", (
                "Renderer mutation of the returned misspellings dict leaked into the "
                "live VocabularyManager._data, get_vocabulary must return a DEEP copy "
                "(IN-31 regression)."
            )
            assert "renderer_only" not in live["misspellings"], (
                "Renderer-added key leaked into the live VocabularyManager._data, "
                "get_vocabulary must return a DEEP copy (IN-31 regression)."
            )
        finally:
            mp.undo()

    def test_get_vocabulary_returns_deep_copy_list_category(self, tmp_path):
        """
        Mutating a returned LIST category (e.g.
        ``phrase_corrections``) must NOT affect the live
        """
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            data = svc.get_vocabulary()
            phrases = data.get("phrase_corrections", [])
            assert isinstance(phrases, list)
            original_len = len(phrases)
            # Mutate the returned list in place + append.
            if phrases:
                phrases[0] = ["MUTATED", "MUTATED"]
            phrases.append(["renderer_only", "added"])
            # The live manager's data must be UNCHANGED.
            live = vm.get_all()
            live_phrases = live["phrase_corrections"]
            assert len(live_phrases) == original_len, (
                "Renderer append to the returned phrase_corrections list leaked into "
                "the live VocabularyManager._data, get_vocabulary must return a DEEP "
                "copy (IN-31 regression)."
            )
            # The bundled "voice to 2 text" entry must be intact.
            assert any(p[0] == "voice to 2 text" for p in live_phrases if isinstance(p, list) and len(p) >= 2), (
                "Renderer mutation of the returned phrase_corrections list corrupted "
                "the live VocabularyManager._data, get_vocabulary must return a DEEP "
                "copy (IN-31 regression)."
            )
        finally:
            mp.undo()

    def test_get_vocabulary_includes_user_file_path(self, tmp_path):
        """The returned dict must include ``_user_file`` pointing at"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            data = svc.get_vocabulary()
            assert "_user_file" in data, "get_vocabulary must include _user_file path"
            assert isinstance(data["_user_file"], str)
            assert "vocabulary.json" in data["_user_file"]
        finally:
            mp.undo()

    def test_get_vocabulary_fallback_when_no_live_manager(self, tmp_path):
        """When ``app._vocabulary_manager`` is None (cold-start /"""
        from voice_typer.server import config as cfg_mod
        from voice_typer.server.service import LausuService

        mp = pytest.MonkeyPatch()
        mp.setattr(cfg_mod, "_config_dir", lambda: tmp_path)
        try:

            class _StubApp:
                config = type("C", (), {"config_dir": tmp_path})()
                tray = MagicMock()
                history_db = None
                # NOTE: no _vocabulary_manager attribute, the
                _template_manager = None
                _llm_polisher = None
                _cloud_engine = None

            svc = LausuService(_StubApp())
            data = svc.get_vocabulary()
            # Fallback read the REAL bundled corrections.json
            assert "misspellings" in data
            # ``recieve -> receive`` is one of the long-standing
            assert data["misspellings"].get("recieve") == "receive", (
                f"Fallback VocabularyManager did not load bundled defaults, "
                f"misspellings.recieve missing from {data['misspellings']!r}"
            )
            assert "_user_file" in data
        finally:
            mp.undo()


class TestTemplateManagerReplaceAll:
    """templates list under the lock, rebuild match indexes, and persist"""

    def test_replace_all_swaps_templates_and_persists(self, tmp_path):
        """replace_all must replace the entire list AND persist to disk"""
        from voice_typer.server.templates import TemplateManager

        mp = pytest.MonkeyPatch()
        patch_config_dir_refs(mp, tmp_path)
        try:
            tm = TemplateManager(config_dir=tmp_path)
            tm.add("old-trigger", "old-output")
            assert len(tm.templates) == 1

            new_templates = [
                {"trigger": "new1", "output": "out1", "match_mode": "exact"},
                {"trigger": "new2", "output": "out2", "match_mode": "contains"},
            ]
            tm.replace_all(new_templates)

            # In-memory list reflects the new templates.
            assert len(tm.templates) == 2
            triggers = {t["trigger"] for t in tm.templates}
            assert triggers == {"new1", "new2"}, f"replace_all did not swap the templates list, got {triggers}"

            # On-disk file reflects the new templates.
            tm2 = TemplateManager(config_dir=tmp_path)
            assert len(tm2.templates) == 2
            triggers2 = {t["trigger"] for t in tm2.templates}
            assert triggers2 == {"new1", "new2"}
        finally:
            mp.undo()

    def test_replace_all_rebuilds_indexes(self, tmp_path):
        """replace_all must call ``_rebuild_indexes`` so the new"""
        from voice_typer.server.templates import TemplateManager

        mp = pytest.MonkeyPatch()
        patch_config_dir_refs(mp, tmp_path)
        try:
            tm = TemplateManager(config_dir=tmp_path)
            tm.add("old-trigger", "old-output")
            assert tm.match("old-trigger") is not None

            tm.replace_all([{"trigger": "fresh-trigger", "output": "fresh-output", "match_mode": "exact"}])

            assert tm.match("old-trigger") is None, (
                "replace_all did not rebuild the match indexes, the old trigger still "
                "matches, which means _exact_index still points at the pre-replace list "
                "(IN-32 regression)."
            )
            # The NEW trigger must match IMMEDIATELY (no restart needed).
            result = tm.match("fresh-trigger")
            assert result is not None
            assert "fresh-output" in result, (
                "replace_all did not rebuild the match indexes, the new trigger is not "
                "matchable until a process restart (IN-32 regression)."
            )
        finally:
            mp.undo()

    def test_replace_all_rolls_back_on_save_failure(self, tmp_path):
        """If ``_save`` raises during replace_all, the in-memory list"""
        from voice_typer.server.templates import TemplateManager

        mp = pytest.MonkeyPatch()
        patch_config_dir_refs(mp, tmp_path)
        try:
            tm = TemplateManager(config_dir=tmp_path)
            tm.add("survivor", "survives-the-failed-save")
            assert len(tm.templates) == 1

            # Patch _save to raise.
            original_save = tm._save
            call_count = {"n": 0}

            def _boom():
                call_count["n"] += 1
                raise OSError("simulated disk full")

            tm._save = _boom
            try:
                with pytest.raises(OSError, match="simulated disk full"):
                    tm.replace_all([{"trigger": "new", "output": "new-out", "match_mode": "exact"}])
            finally:
                tm._save = original_save

            # _save was called exactly once.
            assert call_count["n"] == 1
            # In-memory list was rolled back, the survivor is still there.
            assert len(tm.templates) == 1, (
                "replace_all did not roll back the in-memory list on save failure, "
                "the in-memory state is now out of sync with the on-disk state (IN-32 regression)."
            )
            assert tm.templates[0]["trigger"] == "survivor"
            # The match index still reflects the survivor (rollback
            assert tm.match("survivor") is not None
            assert tm.match("new") is None
        finally:
            mp.undo()

    def test_replace_all_empty_list_clears_everything(self, tmp_path):
        """replace_all with an empty list must clear the in-memory"""
        from voice_typer.server.templates import TemplateManager

        mp = pytest.MonkeyPatch()
        patch_config_dir_refs(mp, tmp_path)
        try:
            tm = TemplateManager(config_dir=tmp_path)
            tm.add("a", "out-a")
            tm.add("b", "out-b")
            assert len(tm.templates) == 2

            tm.replace_all([])

            assert len(tm.templates) == 0
            # Indexes are empty.
            assert tm.match("a") is None
            assert tm.match("b") is None
            # On-disk file is empty.
            tm2 = TemplateManager(config_dir=tmp_path)
            assert len(tm2.templates) == 0
        finally:
            mp.undo()

    def test_replace_all_acquires_lock(self, tmp_path):
        """worker thread that must never see a half-swapped list."""
        from voice_typer.server.templates import TemplateManager

        mp = pytest.MonkeyPatch()
        patch_config_dir_refs(mp, tmp_path)
        try:
            tm = TemplateManager(config_dir=tmp_path)
            # Seed with a baseline set.
            for i in range(20):
                tm.add(f"trigger-{i}", f"output-{i}")

            errors: list[Exception] = []
            stop = threading.Event()

            def matcher():
                while not stop.is_set():
                    try:
                        tm.match(f"trigger-{5}")
                    except Exception as exc:
                        errors.append(exc)
                        return

            t = threading.Thread(target=matcher, daemon=True)
            t.start()
            try:
                # Aggressively replace_all while the matcher runs.
                for i in range(30):
                    new_list = [
                        {"trigger": f"r-{i}-{j}", "output": f"o-{i}-{j}", "match_mode": "exact"} for j in range(10)
                    ]
                    tm.replace_all(new_list)
            finally:
                stop.set()
                t.join(timeout=2.0)

            assert errors == [], (
                "match raised during concurrent replace_all, the lock was not held "
                "for the full swap+rebuild+save sequence (IN-32 regression): {errors}"
            )
        finally:
            mp.undo()


class TestServiceSaveTemplatesUsesReplaceAll:
    """IN-32 (service layer): ``TemplateMixin.save_templates`` must"""

    def test_save_templates_makes_new_trigger_matchable(self, tmp_path):
        """After ``save_templates`` returns, the just-saved templates"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            # Seed with an old template.
            tm.add("old-trigger", "old-output")
            assert tm.match("old-trigger") is not None

            ok = svc.save_templates([{"trigger": "fresh-from-save", "output": "fresh-out", "match_mode": "exact"}])
            assert ok is True

            # The NEW trigger must match IMMEDIATELY (no restart needed).
            result = tm.match("fresh-from-save")
            assert result is not None
            assert "fresh-out" in result, (
                "save_templates did not rebuild the match indexes, the new template "
                "is not matchable until a process restart (IN-32 regression)."
            )
            assert tm.match("old-trigger") is None
        finally:
            mp.undo()

    def test_save_templates_persists_to_disk(self, tmp_path):
        """fresh TemplateManager instance loaded from the same dir sees"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            ok = svc.save_templates(
                [
                    {"trigger": "persisted-1", "output": "out-1", "match_mode": "exact"},
                    {"trigger": "persisted-2", "output": "out-2", "match_mode": "contains"},
                ]
            )
            assert ok is True

            # Fresh instance loads from disk.
            from voice_typer.server.templates import TemplateManager

            tm2 = TemplateManager(config_dir=tmp_path)
            triggers = {t["trigger"] for t in tm2.templates}
            assert triggers == {"persisted-1", "persisted-2"}, (
                f"save_templates did not persist to disk, fresh instance saw {triggers}"
            )
        finally:
            mp.undo()

    def test_save_templates_filters_invalid_entries(self, tmp_path):
        """save_templates must continue to filter invalid entries"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            ok = svc.save_templates(
                [
                    {"trigger": "valid", "output": "out", "match_mode": "exact"},
                    {"trigger": "", "output": "empty-trigger"},  # filtered
                    {"trigger": "no-output", "output": ""},  # filtered
                    {"trigger": "bad-mode", "output": "out", "match_mode": "weird"},  # mode coerced
                    "not-a-dict",  # filtered
                ]
            )
            assert ok is True
            triggers = {t["trigger"] for t in tm.templates}
            assert triggers == {"valid", "bad-mode"}, (
                f"save_templates did not filter invalid entries correctly, got {triggers}"
            )
            bad_mode_tmpl = next(t for t in tm.templates if t["trigger"] == "bad-mode")
            assert bad_mode_tmpl["match_mode"] == "exact"
        finally:
            mp.undo()


class TestGdprInvalidatesManagers:
    """(now-empty) vocabulary / templates files into the live in-memory"""

    def test_gdpr_delete_clears_live_vocabulary_manager(self, tmp_path):
        """After GDPR delete, ``app._vocabulary_manager.get_all()``"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            # Inject PII directly into the live _data (no disk write,
            with vm._lock:
                vm._data["technical_terms"]["my-secret-pii"] = "real-value"
                vm._data["names"]["secret-name"] = "Real Name"
                vm._invalidate_pattern_cache()
            # Verify the PII is in the live _data.
            live = vm.get_all()
            assert live["technical_terms"].get("my-secret-pii") == "real-value"
            assert live["names"].get("secret-name") == "Real Name"

            # The on-disk user vocab file should NOT exist (we never
            vocab_path = tmp_path / "vocabulary.json"
            assert not vocab_path.exists(), (
                "test setup invariant: vocabulary.json should not exist on disk (PII was injected in-memory only)"
            )

            # Run GDPR delete.
            result = svc.delete_all_personal_data()
            assert result["success"] is True, f"GDPR delete failed: {result}"

            # The live in-memory manager must NOT still hold the PII.
            live_after = vm.get_all()
            assert "my-secret-pii" not in live_after["technical_terms"], (
                "GDPR delete did not invalidate the live VocabularyManager, the deleted "
                "PII 'my-secret-pii' is still in _data and would be applied to the next "
                "dictation (IN-33 regression, Art. 17 violation)."
            )
            assert "secret-name" not in live_after["names"], (
                "GDPR delete did not invalidate the live VocabularyManager, the deleted "
                "PII 'secret-name' is still in _data (IN-33 regression, Art. 17 violation)."
            )
            # Bundled defaults are NOT personal data, they must survive.
            assert live_after["misspellings"].get("teh") == "the", (
                "GDPR delete invalidated the bundled defaults, only the user PII should "
                "have been cleared (bundled corrections.json is not personal data)."
            )
        finally:
            mp.undo()

    def test_gdpr_delete_clears_live_template_manager(self, tmp_path):
        """After GDPR delete, ``app._template_manager.templates`` must"""
        svc, app, vm, tm, mp = _make_service(tmp_path)
        try:
            # Inject PII directly into the live _templates (no disk
            with tm._lock:
                tm._templates.extend(
                    [
                        {"trigger": "secret-pii-trigger", "output": "secret-pii-output", "match_mode": "exact"},
                        {"trigger": "another-secret", "output": "another-secret-out", "match_mode": "exact"},
                    ]
                )
                tm._rebuild_indexes()
            assert len(tm.templates) == 2
            # Verify the PII is matchable.
            assert tm.match("secret-pii-trigger") is not None

            # The on-disk templates file should NOT exist (we never
            tmpl_path = tmp_path / "templates.json"
            assert not tmpl_path.exists(), (
                "test setup invariant: templates.json should not exist on disk (PII was injected in-memory only)"
            )

            # Run GDPR delete.
            result = svc.delete_all_personal_data()
            assert result["success"] is True, f"GDPR delete failed: {result}"

            # The live in-memory manager must NOT still hold the PII.
            assert len(tm.templates) == 0, (
                f"GDPR delete did not invalidate the live TemplateManager, the live "
                f"list still has {len(tm.templates)} deleted templates that would be "
                f"matched on the next dictation (IN-33 regression, Art. 17 violation)."
            )
            # The match indexes must also be cleared (no stale matches).
            assert tm.match("secret-pii-trigger") is None, (
                "GDPR delete did not invalidate the live TemplateManager match indexes, "
                "the deleted PII template is still matchable (IN-33 regression)."
            )
        finally:
            mp.undo()

    def test_gdpr_invalidate_managers_helper_exists(self):
        """``PrivacyMixin._gdpr_invalidate_managers`` must exist as a"""
        from voice_typer.server.service.privacy import PrivacyMixin

        assert hasattr(PrivacyMixin, "_gdpr_invalidate_managers"), (
            "PrivacyMixin must define _gdpr_invalidate_managers (IN-33)."
        )
        # It must be callable as a staticmethod (no self required).
        import inspect

        assert isinstance(
            inspect.getattr_static(PrivacyMixin, "_gdpr_invalidate_managers"),
            staticmethod,
        ), "_gdpr_invalidate_managers must be a @staticmethod"

    def test_gdpr_invalidate_managers_handles_none_managers(self, tmp_path):
        """When ``app._vocabulary_manager`` / ``app._template_manager``"""
        from voice_typer.server.service.privacy import PrivacyMixin

        class _StubApp:
            _vocabulary_manager = None
            _template_manager = None

        # Must not raise.
        PrivacyMixin._gdpr_invalidate_managers(_StubApp())

    def test_gdpr_invalidate_managers_handles_missing_attrs(self, tmp_path):
        """When the app doesn't even have ``_vocabulary_manager`` /"""
        from voice_typer.server.service.privacy import PrivacyMixin

        class _StubApp:
            pass  # no manager attributes at all

        # Must not raise.
        PrivacyMixin._gdpr_invalidate_managers(_StubApp())

    def test_gdpr_invalidate_managers_handles_locking_errors(self, tmp_path):
        """If the manager's _load_and_merge / _load raises, the helper"""
        from voice_typer.server.service.privacy import PrivacyMixin

        class _BrokenVM:
            _lock = threading.Lock()

            def _load_and_merge(self):
                raise RuntimeError("simulated vocab reload failure")

        class _BrokenTM:
            _lock = threading.Lock()

            def _load(self):
                raise RuntimeError("simulated templates reload failure")

        class _StubApp:
            _vocabulary_manager = _BrokenVM()
            _template_manager = _BrokenTM()

        # Must not raise, the helper suppresses per-manager exceptions.
        PrivacyMixin._gdpr_invalidate_managers(_StubApp())


class TestServiceSaveTemplatesNoDirectMutation:
    """IN-32 (source-level guard): ``TemplateMixin.save_templates``"""

    def test_save_templates_source_calls_replace_all(self):
        """Source guard: ``save_templates`` must reference"""
        import ast
        import inspect
        import textwrap

        from voice_typer.server.service.template import TemplateMixin

        src = inspect.getsource(TemplateMixin.save_templates)
        # ``inspect.getsource`` returns the method source WITH its
        src = textwrap.dedent(src)
        # Parse the source and walk the AST so we only check actual
        tree = ast.parse(src)
        code_text = ast.unparse(tree)
        assert "tm.replace_all(" in code_text, (
            f"save_templates must call tm.replace_all() (IN-32), code does not reference it. code_text={code_text!r}"
        )
        assert "tm._templates =" not in code_text, (
            "save_templates must NOT directly assign to tm._templates as a code statement "
            "(IN-32 regression), bypasses the lock and skips _rebuild_indexes. "
            f"code_text={code_text!r}"
        )
        assert "tm._save()" not in code_text, (
            "save_templates must NOT call tm._save() directly as a code statement "
            "(IN-32 regression), replace_all handles persistence under the lock. "
            f"code_text={code_text!r}"
        )


class TestServiceGetVocabularyNoThrowaway:
    """IN-31 (source-level guard): ``VocabularyMixin.get_vocabulary``"""

    def test_get_vocabulary_source_uses_live_manager(self):
        """Source guard: ``get_vocabulary`` must reference"""
        import inspect

        from voice_typer.server.service.vocabulary import VocabularyMixin

        src = inspect.getsource(VocabularyMixin.get_vocabulary)
        assert "_vocabulary_manager" in src, (
            "get_vocabulary must reference _vocabulary_manager (IN-31), source does not."
        )
        assert "getattr(self._app" in src, (
            "get_vocabulary must use getattr(self._app, '_vocabulary_manager', None) (IN-31), source does not."
        )
        assert "copy.deepcopy" in src, (
            "get_vocabulary must deep-copy the returned data (IN-31), source does not reference copy.deepcopy."
        )
