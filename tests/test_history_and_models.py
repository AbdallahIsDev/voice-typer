"""Tests for miscellaneous engine infrastructure.

History-retention, history-search, history-db-writer, and model-operation
tests that previously lived here have been split out into dedicated
modules under the Epic EC-25 / Entry #23 test-file split:

* ``tests/test_history_retention.py``  — ``apply_retention`` favorites/trim
* ``tests/test_history_search.py``     — ``HistoryDB.search`` LIKE-escape + length cap
* ``tests/test_history_db_writer.py``  — ``HistoryDBError`` + ``restore()`` writer path
* ``tests/test_model_operations.py``   — model submenu / download cancel / delete / status cache / poll scope

This file retains the remaining miscellaneous engine-infrastructure tests:
vocabulary save retry, corrections load errors, text-cleanup phrase cache,
config validator docstring, ``__main__`` role, tray-icon regressions,
onboarding controller callbacks, templates persistence, keyring status
helper, microphone refresh force, onboarding model switch routing, and
``apply_config`` save-strict persistence contract.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestVocabularySaveRetry:
    """_save_user retries on PermissionError."""

    def test_save_retries_on_permission_error(self, tmp_path):
        import os

        from voice_typer.server.vocabulary import VocabularyManager

        vocab = VocabularyManager(config_dir=tmp_path)
        attempt = {"n": 0}
        real_replace = os.replace

        def flaky_replace(src, dst):
            attempt["n"] += 1
            if attempt["n"] < 3:
                raise PermissionError(f"Simulated lock (attempt {attempt['n']})")
            real_replace(src, dst)

        with patch("os.replace", side_effect=flaky_replace):
            vocab._save_user()

        assert attempt["n"] == 3
        assert (tmp_path / "voice-typer-vocabulary.json").exists()


class TestCorrectionsLoadError:
    """CorrectionsLoadError for malformed corrections file."""

    def test_corrections_load_error_is_runtime_error(self):
        from voice_typer.server.text_cleanup import CorrectionsLoadError

        assert issubclass(CorrectionsLoadError, RuntimeError)

    def test_corrections_load_error_raised_on_malformed_file(self, tmp_path, monkeypatch):
        from voice_typer.server.text_cleanup import (
            CorrectionsLoadError,
            _load_external_corrections,
        )

        path = tmp_path / "voice-typer-corrections.json"
        path.write_text("{not valid json", encoding="utf-8")
        import voice_typer.server.text_cleanup as tc

        monkeypatch.setattr(tc, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        with pytest.raises(CorrectionsLoadError):
            _load_external_corrections(config_dir=tmp_path)


class TestSharedVocabConstants:
    """text_cleanup imports BUNDLED_CORRECTIONS_PATH from vocabulary."""

    def test_bundled_corrections_path_is_same_object(self):
        from voice_typer.server import text_cleanup, vocabulary

        assert text_cleanup._BUNDLED_CORRECTIONS_PATH is vocabulary.BUNDLED_CORRECTIONS_PATH


class TestValidateNonNumericFieldsHasClarifyingDocstring:
    """_validate_non_numeric_fields is NOT a duplicate — it's a migration layer."""

    def test_validator_has_clarifying_docstring(self):
        from voice_typer.server.config import Config

        source = inspect.getsource(Config._validate_non_numeric_fields)
        assert "migration layer" in source


class TestMainModuleDocumentsConsoleScriptRole:
    """__main__.py and console script serve different purposes."""

    def test_main_has_clarifying_docstring(self):
        main_path = REPO_ROOT / "voice_typer" / "__main__.py"
        source = main_path.read_text()
        assert "different purposes" in source.lower() or "NOT a duplicate" in source


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


class TestOnboardingControllerRemovesStepCallbacks:
    """on_step_change and on_complete callbacks removed."""

    def test_no_callbacks_in_init(self):
        from voice_typer.server.onboarding import OnboardingController

        source = inspect.getsource(OnboardingController.__init__)
        assert "self.on_step_change =" not in source
        assert "self.on_complete =" not in source

    def test_next_step_no_callback_invocation(self):
        from voice_typer.server.onboarding import OnboardingController

        source = inspect.getsource(OnboardingController.next_step)
        assert "on_step_change" not in source
        assert "on_complete" not in source


# (TestPhrasePatternCache moved to tests/phrase_patterns/test_phrase_pattern_cache.py
#  and rewritten to test the live ``_get_phrases_regex`` combined-alternation
#  cache after the per-phrase eager-precompiled parallel lists were removed.)


# (TestHistoryRestoreReinsertsRecord moved to tests/test_history_db_writer.py)


class TestTemplatesPersistToDisk:
    """Templates survive to disk via TemplateManager._save."""

    def test_templates_persisted_to_json_file(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=templates_dir)
        tm.add("hello", "Hello World!")
        tm.add("bye", "Goodbye!", match_mode="contains")

        templates_file = templates_dir / "voice-typer-templates.json"
        assert templates_file.exists()
        import json

        data = json.loads(templates_file.read_text(encoding="utf-8"))
        assert "templates" in data
        assert len(data["templates"]) == 2
        assert data["templates"][0]["trigger"] == "hello"
        assert data["templates"][1]["match_mode"] == "contains"

    def test_templates_loaded_on_restart(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm1 = TemplateManager(config_dir=templates_dir)
        tm1.add("persist_test", "persisted value")
        del tm1

        tm2 = TemplateManager(config_dir=templates_dir)
        templates = tm2.templates
        assert len(templates) == 1
        assert templates[0]["trigger"] == "persist_test"
        assert templates[0]["output"] == "persisted value"

    def test_service_save_and_get_round_trip(self, templates_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())

        templates_to_save = [
            {"trigger": "my_email", "output": "me@example.com", "match_mode": "exact"},
            {"trigger": "signature", "output": "Best regards,\nJohn", "match_mode": "contains"},
        ]
        assert service.save_templates(templates_to_save) is True

        FakeApp._template_manager = None
        service2 = VoiceTyperService(FakeApp())
        loaded = service2.get_templates()
        assert len(loaded) == 2
        assert loaded[0]["trigger"] == "my_email"
        assert loaded[0]["output"] == "me@example.com"
        assert loaded[1]["match_mode"] == "contains"

    def test_service_save_rejects_invalid_entries(self, templates_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())
        bad_input = [
            {"trigger": "valid", "output": "ok", "match_mode": "exact"},
            {"trigger": "", "output": "missing trigger"},
            {"trigger": "missing output", "output": ""},
            {"trigger": "bad mode", "output": "ok", "match_mode": "invalid"},
            "not a dict",
            None,
        ]
        assert service.save_templates(bad_input) is True
        loaded = service.get_templates()
        assert len(loaded) == 2
        triggers = [t["trigger"] for t in loaded]
        assert "valid" in triggers
        assert "bad mode" in triggers
        bad_mode_entry = next(t for t in loaded if t["trigger"] == "bad mode")
        assert bad_mode_entry["match_mode"] == "exact"


# regression tests (SVC-2/6/7/8/9/10/11, PERF-21) ────────────
#
# Each test class below pins one of the SVC-N fixes applied to
# ``voice_typer/server/service.py`` in FIX sub-agent #5. Tests are
# intentionally narrow (unit-level) — they exercise the service in
# isolation against a FakeApp/FakeConfig so they don't pull in the
# heavy app/conftest autouse-mock machinery.


class TestKeyringStatusHelper:
    """SVC-6: ``_keyring_status()`` centralizes the duplicated probe."""

    def test_returns_dict_with_expected_keys(self, tmp_config_dir, monkeypatch):
        """``_keyring_status`` returns a dict containing the four
        keys the renderer reads (``available``/``backend``/``fallback``/
        ``reason``)."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(
            cs,
            "get_keyring_status",
            lambda: {
                "available": True,
                "backend": "SecretServiceKeyring",
                "fallback": False,
                "reason": None,
            },
        )
        result = service._keyring_status()
        assert result == {
            "available": True,
            "backend": "SecretServiceKeyring",
            "fallback": False,
            "reason": None,
        }

    def test_returns_fallback_when_credential_store_raises(self, tmp_config_dir, monkeypatch):
        """When ``credential_store.get_keyring_status`` raises, the
        helper returns a safe ``{available: False, fallback: True, ...}``
        dict so the IPC ``get_config`` path never breaks."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())

        def _boom():
            raise RuntimeError("keychain exploded")

        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "get_keyring_status", _boom)
        result = service._keyring_status()
        assert result["available"] is False
        assert result["backend"] is None
        assert result["fallback"] is True
        assert "keychain exploded" in result["reason"]

    def test_get_config_and_get_defaults_share_helper(self, tmp_config_dir, monkeypatch):
        """Both ``get_config`` and ``get_defaults`` route through
        ``_keyring_status`` — patching the helper once affects both
        callers (proves the duplication was actually removed)."""
        from voice_typer.server.service import VoiceTyperService

        calls: list[int] = []

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())

        def _spy(self):
            calls.append(1)
            return {"available": False, "backend": None, "fallback": True, "reason": "spy"}

        monkeypatch.setattr(VoiceTyperService, "_keyring_status", _spy)

        # the sanitizer moved out of ``ipc_server``
        # into the transport-neutral ``config_sanitizer`` module.
        # Patch both symbols so the test stays valid against either
        # import path (legacy ``ipc_server._sanitize_config_for_ipc``
        # alias and the current canonical location).
        import voice_typer.server.config_sanitizer as cfg_san
        import voice_typer.server.ipc_server as ipc

        monkeypatch.setattr(ipc, "_sanitize_config_for_ipc", lambda c: {})
        monkeypatch.setattr(cfg_san, "sanitize_config_for_ipc", lambda c: {})

        import voice_typer.server.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "Config", lambda: object())

        service.get_config()
        service.get_defaults()
        assert len(calls) == 2, (
            f"Expected _keyring_status to be called once per get_config + "
            f"once per get_defaults (2 total), got {len(calls)}"
        )


# (TestDeleteModelUsesRegistryUnconditionally moved to tests/test_model_operations.py)


class TestRefreshMicrophonesForce:
    """SVC-8: ``refresh_microphones(force=True)`` bypasses the 5 s TTL
    cache so callers that *know* a hot-plug event happened can refresh
    immediately."""

    def _make_service(self, monkeypatch, mics_by_call):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            def __init__(self):
                self._microphones = []
                self.tray = type(
                    "FakeTray",
                    (),
                    {"set_microphones": staticmethod(lambda m: None)},
                )()

        service = VoiceTyperService(FakeApp())

        def _fake_list_microphones():
            return mics_by_call.pop(0)

        monkeypatch.setattr(
            "voice_typer.server.server_platform.list_microphones",
            _fake_list_microphones,
        )
        return service

    def test_default_call_uses_cache_within_ttl(self, tmp_config_dir, monkeypatch):
        """Two calls within the 5 s window return the SAME list — the
        second call is served from cache, so PortAudio is queried only
        once."""
        mics_v1 = [{"id": 0, "name": "Built-in"}]
        mics_v2 = [{"id": 0, "name": "Built-in"}, {"id": 5, "name": "USB"}]
        service = self._make_service(monkeypatch, [mics_v1, mics_v2])

        first = service.refresh_microphones()
        second = service.refresh_microphones()
        assert first == mics_v1
        assert second == mics_v1, "Second call within 5s should be served from cache (same list)"

    def test_force_bypasses_cache(self, tmp_config_dir, monkeypatch):
        """``refresh_microphones(force=True)`` ignores the cache and
        re-queries PortAudio, picking up newly-plugged devices."""
        mics_v1 = [{"id": 0, "name": "Built-in"}]
        mics_v2 = [{"id": 0, "name": "Built-in"}, {"id": 5, "name": "USB"}]
        service = self._make_service(monkeypatch, [mics_v1, mics_v2])

        first = service.refresh_microphones()
        assert first == mics_v1

        cached = service.refresh_microphones()
        assert cached == mics_v1

        forced = service.refresh_microphones(force=True)
        assert forced == mics_v2, "force=True must bypass the TTL cache and re-query PortAudio"


# (TestGetModelStatusCache moved to tests/test_model_operations.py)


class TestOnboardingUsesServiceChangeModel:
    """SVC-10: ``onboarding_apply`` routes the model switch through
    ``self.change_model`` (the ADR-0008-§3.1 service-layer wrapper)
    instead of reaching into ``app.models.change_model`` directly."""

    def test_calls_self_change_model_not_app_models_directly(self, tmp_config_dir, monkeypatch):
        """When the user picks a non-default model in onboarding,
        ``onboarding_apply`` invokes ``self.change_model`` (which goes
        through ``app.change_model`` -> ``app.models.change_model``)."""
        import voice_typer.server.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "publish", lambda msg: True)

        import contextlib

        from voice_typer.server.service import VoiceTyperService

        app = MagicMock()
        app.config.onboarding_completed = False
        app.config.model_size = "small.en"
        app.config.save = MagicMock(return_value=True)

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app._config_mutation_lock = _fake_lock()

        service = VoiceTyperService(app)

        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController()
        ctrl.set_hotkey("<f6>")
        ctrl.set_model("tiny.en")
        service._onboarding = ctrl

        service.onboarding_apply()

        (
            app.change_model.assert_called_once_with("tiny.en"),
            (
                "onboarding_apply should route model switch through "
                "self.change_model (SVC-10) which delegates to app.change_model"
            ),
        )


class TestApplyConfigPersistsOnSideEffectFailure:
    """SVC-11: ``apply_config`` persists config via ``save_strict()``.

    PVT-21 (session-1) extracted ``apply_config`` orchestration from
    ``service.py`` into ``config_applier.py``. The new contract:

    1. ``service.apply_config`` delegates to ``config_applier.apply_config``.
    2. ``config_applier.apply_config`` calls ``apply_config_side_effects``
       then ``app.config.save_strict()`` (NOT ``save()``).
    3. ``save_strict()`` raises ``RuntimeError`` if ``save()``
       returned ``False`` (disk write failure) — the IPC handler is
       expected to catch this and surface the error.
    4. G4-H-12: if ``save_strict()`` raises, in-memory Config is rolled
       back to the pre-setattr snapshot so live state matches disk.

    The original SVC-11 "save in finally even when side-effects raise"
    guarantee is replaced by the G4-H-12 rollback pattern: if side-effects
    raise, ``save_strict()`` is NOT called (the raise propagates first),
    but in-memory state is rolled back so it cannot diverge from disk.
    """

    def _make_service_and_app(self):
        import contextlib

        from voice_typer.server.service import VoiceTyperService

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app = MagicMock()
        app._config_mutation_lock = _fake_lock()
        app.config = MagicMock()
        # + : config_applier now calls save_strict() (not save()).
        # save_strict() raises RuntimeError if save() returns False.
        app.config.save = MagicMock(return_value=True)
        app.config.save_strict = MagicMock(return_value=None)
        app.clipboard = MagicMock()
        app.tray = MagicMock()
        app.tray.invalidate_menu_cache = MagicMock()
        app._llm_polisher = None
        app.hotkeys = MagicMock()
        app.recorder = MagicMock()
        app._busy_event = MagicMock()
        app._busy_event.is_set = MagicMock(return_value=True)
        app._shutting_down = False

        service = VoiceTyperService(app)
        return service, app

    def test_save_called_when_side_effects_succeed(self, tmp_config_dir, monkeypatch):
        """Baseline: when side effects succeed, save_strict() is called once."""
        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        # config_applier imports asdict INSIDE apply_config; patch dataclasses.asdict.
        # Use a counter so pre != post (state appears changed) → save_strict called.
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        service.apply_config({"hotkey": "<f4>"})
        app.config.save_strict.assert_called_once_with()

    def test_save_called_when_side_effects_raise(self, tmp_config_dir, monkeypatch):
        """PVT-21: when ``apply_config_side_effects`` raises, the raise
        propagates and ``save_strict()`` is NOT called. The original
        exception is re-raised so the IPC layer can surface the error.
        (Replaces the SVC-11 "save in finally" guarantee — see class docstring.)"""
        import pytest

        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        def _boom(updates):
            raise RuntimeError("side effect blew up")

        # side-effects now live on config_applier, not service.
        monkeypatch.setattr(service._config_applier, "apply_config_side_effects", _boom)

        with pytest.raises(RuntimeError, match="side effect blew up"):
            service.apply_config({"hotkey": "<f4>"})

        # Side-effects raised → save_strict NOT called (raise propagated first).
        app.config.save_strict.assert_not_called()

    def test_save_failure_surfaces_when_side_effects_succeeded(self, tmp_config_dir, monkeypatch):
        """if save_strict() raises (e.g. save() returned False →
        RuntimeError, or underlying OSError propagates), the error is
        surfaced to the caller. G4-H-12: in-memory Config is rolled back."""

        import pytest

        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        # save_strict raises when save() returned False.
        # We mock save_strict to raise OSError directly to simulate
        # a disk-write failure that propagated (rather than the
        # save-returns-False → save_strict-raises-RuntimeError path).
        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": "<f4>"})


# (TestDownloadPollScopedToModelDir moved to tests/test_model_operations.py)
