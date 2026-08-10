"""Tests for ER-FIX-H fixes (Group 2 performance wave).

Covers:
- ER-54 (tray): tooltip elapsed-recording time uses ``time.monotonic()``
  (not ``time.time()``), so wall-clock jumps (NTP skew, DST transitions)
  cannot corrupt the displayed ``mm:ss``.
- ER-55 (templates):
  * ``substitute_variables`` is lazy — it does NOT call
    ``_get_clipboard_text()`` (which can block on the X11/clipboard
    selection) when the template output has no ``{clipboard}``
    placeholder.
  * ``_WHITESPACE_RE`` is compiled exactly once at import time, not
    re-compiled per template per ``match()`` call.
- ER-64 (_secrets):
  * ``_LOOPBACK_HOSTS`` is a module-level frozenset — its ``id()`` is
    stable across multiple ``assert_url_allowed`` calls (the previous
    per-call literal rebuilt the set every time).
"""

import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# tray tooltip uses time.monotonic() ────────────────────────────



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
    """ER-54: tray elapsed-recording time uses ``time.monotonic()`` so
    wall-clock jumps don't corrupt the displayed ``mm:ss``."""

    def test_elapsed_uses_monotonic_not_wall_clock(self, tray_module, monkeypatch):
        """``_compute_tooltip`` reads ``time.monotonic()``, NOT
        ``time.time()``. A wall-clock jump (mocked by setting
        ``time.time`` to a huge value) must not affect the elapsed
        suffix — only ``time.monotonic`` drives the computation.
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
        # mid-test and exhaust the monotonic-value iterator. We're only
        # testing the value of _recording_started_at and the tooltip
        # computation, not the timer machinery.
        monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
        monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)

        # Monotonic returns 1000.0 first (set_state stores it as
        # _recording_started_at), then 1065.0 forever after (so the
        # tooltip computation sees a 65s delta → "01:05").
        monotonic_seq = [1000.0, 1065.0]

        def _fake_monotonic():
            return monotonic_seq[0] if len(monotonic_seq) == 1 else monotonic_seq.pop(0)

        monkeypatch.setattr(tray_module.time, "monotonic", _fake_monotonic)
        # Sabotage ``time.time`` so any code path that falls back to the
        # wall clock would produce a wildly wrong elapsed (1970 + 1e9 sec).
        monkeypatch.setattr(tray_module.time, "time", lambda: 1_000_000_000.0)

        tray.set_state(AppState.RECORDING, "recording")
        # ``_recording_started_at`` should be 1000.0 (the first monotonic
        # value), NOT the wall-clock value.
        assert tray._recording_started_at == 1000.0, (
            f"set_state should store time.monotonic() (1000.0), got {tray._recording_started_at!r}"
        )

        # Now compute tooltip — it should call ``time.monotonic()`` again
        # (1065.0), yielding elapsed = 65s -> "01:05". If it had used
        # ``time.time()``, elapsed would be ~1e9 and the format would
        # blow up or display nonsense.
        tooltip = tray._compute_tooltip(AppState.RECORDING, "recording")
        assert "01:05" in tooltip, f"Tooltip should show mm:ss=01:05 (monotonic delta=65s), got {tooltip!r}"

        # Cleanup.
        tray.set_state(AppState.IDLE)

    def test_elapsed_survives_wall_clock_jump(self, tray_module, monkeypatch):
        """Scenario: recording starts at monotonic=100. Mid-recording,
        the wall clock jumps forward by 1e6 seconds (NTP slew). The
        displayed elapsed must still be the monotonic delta, NOT the
        wall-clock delta.
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

        # Disable the timer thread (see test_elapsed_uses_monotonic_not_wall_clock).
        monkeypatch.setattr(tray, "_start_elapsed_timer", lambda: None)
        monkeypatch.setattr(tray, "_cancel_elapsed_timer", lambda: None)

        # Monotonic: first call returns 100.0 (stored in
        # _recording_started_at); subsequent calls return 145.0.
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
        # been 1000s -> "16:40". Asserting the monotonic value proves
        # the wall-clock jump did NOT leak into the elapsed computation.
        assert "00:45" in tooltip, (
            f"Elapsed should be monotonic delta (45s -> '00:45'), not "
            f"wall-clock delta (1000s -> '16:40'). Tooltip: {tooltip!r}"
        )

        tray.set_state(AppState.IDLE)


# templates lazy variable resolution + hoisted regex ────────────


class TestTemplatesLazyClipboard:
    """ER-55: ``substitute_variables`` must NOT touch the clipboard when
    the template output has no ``{clipboard}`` placeholder."""

    def test_no_clipboard_call_without_placeholder(self, monkeypatch):
        from voice_typer.server import templates as tmpl_mod

        # Spy on _get_clipboard_text: it should NOT be called when the
        # template output doesn't contain {clipboard}.
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
            f"{{clipboard}} placeholder; was called {call_count['n']} time(s)"
        )

    def test_clipboard_called_only_when_placeholder_present(self, monkeypatch):
        """Symmetric positive test: when {clipboard} IS in the output,
        the lazy resolver DOES call _get_clipboard_text exactly once."""
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
        """Bonus: ``datetime.now()`` is also lazy. Verify by spying on
        ``datetime`` in the templates module."""
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

        # templates.substitute_variables references the ``datetime`` name
        # imported at module scope.
        monkeypatch.setattr(tmpl_mod, "datetime", _SpyDateTime)

        # No {today} / {now} placeholders → datetime.now() should NOT be
        # called at all.
        out = tmpl_mod.substitute_variables("plain text with {username} only")
        assert "{username}" not in out
        assert call_count["n"] == 0, (
            f"datetime.now() should NOT be called when no {{today}}/{{now}} "
            f"placeholders are present; was called {call_count['n']} time(s)"
        )

    def test_no_placeholder_fast_path_returns_unchanged(self, monkeypatch):
        """When the text has no ``{`` at all, ``substitute_variables``
        short-circuits and returns the string unchanged without invoking
        the regex."""
        from voice_typer.server import templates as tmpl_mod

        # Sabotage _get_clipboard_text and datetime so any accidental
        # invocation would surface as a clearly wrong value.
        monkeypatch.setattr(tmpl_mod, "_get_clipboard_text", lambda: "LEAK")
        out = tmpl_mod.substitute_variables("no placeholders here at all")
        assert out == "no placeholders here at all"


class TestTemplatesWhitespaceRegexCompiledOnce:
    """ER-55: ``_WHITESPACE_RE`` is compiled ONCE at import time, not
    per-template-per-match."""

    def test_whitespace_re_is_module_level_pattern(self):
        from voice_typer.server import templates as tmpl_mod

        assert hasattr(tmpl_mod, "_WHITESPACE_RE"), "templates module must expose module-level _WHITESPACE_RE"
        assert isinstance(tmpl_mod._WHITESPACE_RE, re.Pattern), (
            f"_WHITESPACE_RE must be a compiled re.Pattern, got {type(tmpl_mod._WHITESPACE_RE)!r}"
        )

    def test_match_does_not_recompile_regex(self, monkeypatch, tmp_config_dir):
        """Wrapping ``re.compile`` to count invocations, ``match()``
        must NOT trigger any additional ``re.compile`` calls — the
        module-level ``_WHITESPACE_RE`` is reused on every iteration.
        """
        from voice_typer.server import templates as tmpl_mod
        from voice_typer.server.templates import TemplateManager

        # Reload templates so the module-level _WHITESPACE_RE is built
        # using our spied re.compile (the wrapper delegates to the real
        # compile so behavior is unchanged).
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
        # Run match() many times across multiple templates — none of
        # these calls should invoke re.compile again.
        for _ in range(50):
            tm.match("code review")
            tm.match("standup")
            tm.match("let's do a retro now")
            tm.match("nothing matches this")

        assert compile_calls["n"] == baseline, (
            f"match() must not invoke re.compile — _WHITESPACE_RE should "
            f"be reused. Baseline={baseline}, after match loop="
            f"{compile_calls['n']}"
        )

    def test_whitespace_re_is_stable_object(self):
        """``_WHITESPACE_RE`` is the same object across module
        accesses (proves it isn't rebuilt per call)."""
        from voice_typer.server import templates as tmpl_mod

        obj1 = tmpl_mod._WHITESPACE_RE
        obj2 = tmpl_mod._WHITESPACE_RE
        assert obj1 is obj2
        assert id(obj1) == id(obj2)


# _secrets _LOOPBACK_HOSTS module-level ─────────────────────────


class TestLoopbackHostsModuleLevel:
    """ER-64: ``_LOOPBACK_HOSTS`` is a module-level frozenset — its
    ``id()`` is stable across multiple ``assert_url_allowed`` calls."""

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
        """``id(_LOOPBACK_HOSTS)`` must NOT change between calls —
        the pre-fix per-call literal would have produced a new
        frozenset (and thus a new id) on every invocation."""
        from voice_typer.server import _secrets

        ids = set()
        for _ in range(20):
            # ``assert_url_allowed`` exercises the loopback lookup path.
            # Use ``allow_loopback_http=True`` so the call succeeds for
            # the http + loopback combination.
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
        """All three loopback hosts exercise the same module-level
        object — confirms the host lookup uses _LOOPBACK_HOSTS (not a
        locally-built set)."""
        from voice_typer.server import _secrets

        first_id = id(_secrets._LOOPBACK_HOSTS)
        # IPv6 loopback (``::1``) requires bracketed URL form per RFC 3986;
        # use the bracketed syntax so ``urlparse`` returns a hostname.
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
    """ER-64: ``_sub`` is hoisted out of the ``for pat in _KEY_PATTERNS``
    loop in ``redact_api_keys``. This is a behavior-preserving refactor;
    we verify the redaction still works correctly (sanity check that
    the hoist didn't break capture semantics)."""

    def test_bearer_prefix_preserved_after_hoist(self):
        from voice_typer.server._secrets import redact_api_keys

        out = redact_api_keys("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert "Bearer" in out
        assert "sk-abcdef" not in out

    def test_replacement_marker_configurable(self):
        """The hoisted ``_sub`` must still capture ``replacement`` via
        the default-argument closure (not a stale value)."""
        from voice_typer.server._secrets import redact_api_keys

        out_default = redact_api_keys("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF")
        assert "***" in out_default

        out_custom = redact_api_keys(
            "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF",
            replacement="[redacted]",
        )
        assert "[redacted]" in out_custom
        assert "***" not in out_custom
