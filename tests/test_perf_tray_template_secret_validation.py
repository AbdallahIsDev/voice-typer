"""Tests for the tray, template, and secret-validation performance fixes."""

import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tray_module(monkeypatch):
    """Import (or reload) the tray module with pystray + PIL mocked."""
    import voice_typer.server.tray as tray_mod

    mock_pystray = MagicMock()
    mock_pystray.Icon = MagicMock
    mock_pystray.Menu = MagicMock
    mock_pystray.Menu.SEPARATOR = "SEP"
    mock_pystray.MenuItem = MagicMock
    monkeypatch.setattr(tray_mod, "pystray", mock_pystray)

    mock_pil = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", mock_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=0: MagicMock())
    return tray_mod


class TestTrayMonotonicElapsed:
    """ER-54: tray elapsed-recording time uses ``time.monotonic()`` so"""

    def test_elapsed_uses_monotonic_not_wall_clock(self, tray_module, monkeypatch):
        """
        ``_compute_tooltip`` reads ``time.monotonic()``, NOT
        ``time.time`` to a huge value) must not affect the elapsed
        """
        from voice_typer.server.tray import AppState, TrayIcon

        controller = MagicMock()
        tray = TrayIcon(
            controller=controller,
            config=SimpleNamespace(
                hotkey="<f2>",
                model_size="small.en",
                autostart=True,
                show_notifications=True,
                microphone=None,
                silence_warning_seconds=20.0,
                stop_on_silence_seconds=120.0,
            ),
        )

        # Disable the elapsed-recording Timer thread so it can't fire
        monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
        monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)

        monotonic_seq = [1000.0, 1065.0]

        def _fake_monotonic():
            return monotonic_seq[0] if len(monotonic_seq) == 1 else monotonic_seq.pop(0)

        monkeypatch.setattr(tray_module.time, "monotonic", _fake_monotonic)
        monkeypatch.setattr(tray_module.time, "time", lambda: 1_000_000_000.0)

        tray.set_state(AppState.RECORDING, "recording")
        # ``_recording_started_at`` should be 1000.0 (the first monotonic
        assert tray._recording_started_at == 1000.0, (
            f"set_state should store time.monotonic() (1000.0), got {tray._recording_started_at!r}"
        )

        # Now compute tooltip, it should call ``time.monotonic()`` again
        tooltip = tray._compute_tooltip(AppState.RECORDING, "recording")
        assert "01:05" in tooltip, f"Tooltip should show mm:ss=01:05 (monotonic delta=65s), got {tooltip!r}"

        # Cleanup.
        tray.set_state(AppState.IDLE)

    def test_elapsed_survives_wall_clock_jump(self, tray_module, monkeypatch):
        """Scenario: recording starts at monotonic=100. Mid-recording,"""
        from voice_typer.server.tray import AppState, TrayIcon

        controller = MagicMock()
        tray = TrayIcon(
            controller=controller,
            config=SimpleNamespace(
                hotkey="<f2>",
                model_size="small.en",
                autostart=True,
                show_notifications=True,
                microphone=None,
                silence_warning_seconds=20.0,
                stop_on_silence_seconds=120.0,
            ),
        )

        # Disable the timer thread (see test_elapsed_uses_monotonic_not_wall_clock).
        monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
        monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)

        monotonic_seq = [100.0, 145.0]

        def _fake_monotonic():
            return monotonic_seq[0] if len(monotonic_seq) == 1 else monotonic_seq.pop(0)

        monkeypatch.setattr(tray_module.time, "monotonic", _fake_monotonic)
        # Wall clock jumps absurdly between set_state and _compute_tooltip.
        wall_seq = [1_700_000_000.0, 1_700_001_000.0]

        def _fake_wall():
            return wall_seq[0] if len(wall_seq) == 1 else wall_seq.pop(0)

        monkeypatch.setattr(tray_module.time, "time", _fake_wall)

        tray.set_state(AppState.RECORDING, "recording")
        tooltip = tray._compute_tooltip(AppState.RECORDING, "recording")
        # Monotonic delta = 45s -> "00:45". Wall-clock delta would have
        assert "00:45" in tooltip, (
            f"Elapsed should be monotonic delta (45s -> '00:45'), not "
            f"wall-clock delta (1000s -> '16:40'). Tooltip: {tooltip!r}"
        )

        tray.set_state(AppState.IDLE)


class TestTemplatesLazyClipboard:
    """ER-55: ``substitute_variables`` must NOT touch the clipboard when"""

    def test_no_clipboard_call_without_placeholder(self, monkeypatch):
        from voice_typer.server import templates as tmpl_mod

        call_count = {"n": 0}

        def _spy():
            call_count["n"] += 1
            return "SPY_CLIPBOARD"

        monkeypatch.setattr(tmpl_mod, "_get_clipboard_text", _spy)

        # Output uses {today} and {username} but NOT {clipboard}.
        out = tmpl_mod.substitute_variables("meeting on {today} with {username}")
        assert "SPY_CLIPBOARD" not in out
        assert "{today}" not in out
        assert "{username}" not in out
        assert call_count["n"] == 0, (
            f"_get_clipboard_text should NOT be called when output has no "
            f"{{clipboard}} placeholder; was called {call_count['n']} times"
        )

    def test_clipboard_called_only_when_placeholder_present(self, monkeypatch):
        """Symmetric positive test: when {clipboard} IS in the output,"""
        from voice_typer.server import templates as tmpl_mod

        call_count = {"n": 0}

        def _spy():
            call_count["n"] += 1
            return "CLIP_VAL"

        monkeypatch.setattr(tmpl_mod, "_get_clipboard_text", _spy)
        out = tmpl_mod.substitute_variables("paste: {clipboard}")
        assert "CLIP_VAL" in out
        assert call_count["n"] == 1

    def test_no_datetime_call_without_placeholder(self, monkeypatch):
        """Bonus: ``datetime.now()`` is also lazy. Verify by spying on"""
        from datetime import datetime as real_dt

        from voice_typer.server import templates as tmpl_mod

        call_count = {"n": 0}

        class _SpyDateTime:
            @classmethod
            def now(cls):
                call_count["n"] += 1
                return real_dt.now()

            def strftime(self, fmt):
                return real_dt.strftime(self, fmt)

        monkeypatch.setattr(tmpl_mod, "datetime", _SpyDateTime)

        # No {today} / {now} placeholders → datetime.now() should NOT be
        out = tmpl_mod.substitute_variables("plain text with {username} only")
        assert "{username}" not in out
        assert call_count["n"] == 0, (
            f"datetime.now() should NOT be called when no {{today}}/{{now}} "
            f"placeholders are present; was called {call_count['n']} times"
        )

    def test_no_placeholder_fast_path_returns_unchanged(self, monkeypatch):
        """When the text has no ``{`` at all, ``substitute_variables``"""
        from voice_typer.server import templates as tmpl_mod

        # Sabotage _get_clipboard_text and datetime so any accidental
        monkeypatch.setattr(tmpl_mod, "_get_clipboard_text", lambda: "LEAK")
        out = tmpl_mod.substitute_variables("no placeholders here at all")
        assert out == "no placeholders here at all"


class TestTemplatesWhitespaceRegexCompiledOnce:
    """ER-55: ``_WHITESPACE_RE`` is compiled ONCE at import time, not"""

    def test_whitespace_re_is_module_level_pattern(self):
        from voice_typer.server import templates as tmpl_mod

        assert hasattr(tmpl_mod, "_WHITESPACE_RE"), "templates module must expose module-level _WHITESPACE_RE"
        assert isinstance(tmpl_mod._WHITESPACE_RE, re.Pattern), (
            f"_WHITESPACE_RE must be a compiled re.Pattern, got {type(tmpl_mod._WHITESPACE_RE)!r}"
        )

    def test_match_does_not_recompile_regex(self, monkeypatch, tmp_config_dir):
        """Wrapping ``re.compile`` to count invocations, ``match()``"""
        from voice_typer.server import templates as tmpl_mod
        from voice_typer.server.templates import TemplateManager

        # Reload templates so the module-level _WHITESPACE_RE is built
        original_compile = re.compile
        compile_calls = {"n": 0}

        def _spy_compile(*args, **kwargs):
            compile_calls["n"] += 1
            return original_compile(*args, **kwargs)

        monkeypatch.setattr(tmpl_mod.re, "compile", _spy_compile)

        tm = TemplateManager(config_dir=tmp_config_dir)
        tm.add("code review", "Please review this code.")
        tm.add("standup", "Standup notes.")
        tm.add("retro", "Retro items.", match_mode="contains")

        baseline = compile_calls["n"]
        for _ in range(50):
            tm.match("code review")
            tm.match("standup")
            tm.match("let's do a retro now")
            tm.match("nothing matches this")

        assert compile_calls["n"] == baseline, (
            f"match() must not invoke re.compile, _WHITESPACE_RE should "
            f"be reused. Baseline={baseline}, after match loop="
            f"{compile_calls['n']}"
        )

    def test_whitespace_re_is_stable_object(self):
        """``_WHITESPACE_RE`` is the same object across module"""
        from voice_typer.server import templates as tmpl_mod

        obj1 = tmpl_mod._WHITESPACE_RE
        obj2 = tmpl_mod._WHITESPACE_RE
        assert obj1 is obj2
        assert id(obj1) == id(obj2)


# _secrets _LOOPBACK_HOSTS module-level ─────────────────────────


class TestLoopbackHostsModuleLevel:
    """ER-64: ``_LOOPBACK_HOSTS`` is a module-level frozenset, its"""

    def test_loopback_hosts_is_module_level(self):
        from voice_typer.server import _secrets

        assert hasattr(_secrets, "_LOOPBACK_HOSTS"), "_secrets module must expose module-level _LOOPBACK_HOSTS"
        assert isinstance(_secrets._LOOPBACK_HOSTS, frozenset), (
            f"_LOOPBACK_HOSTS must be a frozenset, got {type(_secrets._LOOPBACK_HOSTS)!r}"
        )
        assert "localhost" in _secrets._LOOPBACK_HOSTS
        assert "127.0.0.1" in _secrets._LOOPBACK_HOSTS
        assert "::1" in _secrets._LOOPBACK_HOSTS

    def test_loopback_hosts_id_stable_across_calls(self):
        """``id(_LOOPBACK_HOSTS)`` must NOT change between calls —"""
        from voice_typer.server import _secrets

        ids = set()
        for _ in range(20):
            # ``assert_url_allowed`` exercises the loopback lookup path.
            _secrets.assert_url_allowed(
                "http://localhost:11434",
                allow_loopback_http=True,
            )
            ids.add(id(_secrets._LOOPBACK_HOSTS))

        assert len(ids) == 1, (
            f"_LOOPBACK_HOSTS id must be stable across calls (pre-fix "
            f"rebuilt the frozenset each time). Got {len(ids)} distinct "
            f"ids: {ids}"
        )

    def test_loopback_hosts_id_stable_across_loopback_variants(self):
        """All three loopback hosts exercise the same module-level"""
        from voice_typer.server import _secrets

        first_id = id(_secrets._LOOPBACK_HOSTS)
        # IPv6 loopback (``::1``) requires bracketed URL form per RFC 3986;
        for url in (
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://[::1]:11434",
        ):
            _secrets.assert_url_allowed(
                url,
                allow_loopback_http=True,
            )
            assert id(_secrets._LOOPBACK_HOSTS) == first_id, f"_LOOPBACK_HOSTS id drifted after checking URL {url!r}"


class TestRedactApiKeysSubHoisted:
    """ER-64: ``_sub`` is hoisted out of the ``for pat in _KEY_PATTERNS``"""

    def test_bearer_prefix_preserved_after_hoist(self):
        from voice_typer.server._secrets import redact_api_keys

        out = redact_api_keys("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert "Bearer" in out
        assert "sk-abcdef" not in out

    def test_replacement_marker_configurable(self):
        """The hoisted ``_sub`` must still capture ``replacement`` via"""
        from voice_typer.server._secrets import redact_api_keys

        out_default = redact_api_keys("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF")
        assert "***" in out_default

        out_custom = redact_api_keys(
            "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF",
            replacement="[redacted]",
        )
        assert "[redacted]" in out_custom
        assert "***" not in out_custom
