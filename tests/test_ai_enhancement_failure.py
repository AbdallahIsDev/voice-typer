"""Failure-path tests for the enhancement-steps mixin.

Covers ``_apply_ai_enhancement`` (Step 7b, rule-based) and
``_apply_llm_polish`` (Step 7, LLM) failure handling:

* Rule-based enhancer failure must (1) return the ORIGINAL text (the
  pipeline contract: failures degrade to un-enhanced text, never abort
  the cycle), (2) publish ``text_enhancement_failed``, NOT
  ``llm_polish_failed`` (the E9-class event-type mismatch this fix
  removes), and (3) survive a raising event bus (suppress-wrap, so a
  broken bus can never abort the whole dictation).
* LLM-polish failure must still publish ``llm_polish_failed`` (its
  correct owner) and notify once per session via the tray.
"""

from __future__ import annotations

import types

import pytest
from voice_typer.server import event_bus
from voice_typer.server.dictation_pipeline.enhancement_steps import _EnhancementStepsMixin


class _FakeTray:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.notifications.append((title, message))


class _FakeApp:
    def __init__(self, config: types.SimpleNamespace) -> None:
        self.config = config
        self.tray = _FakeTray()
        self._llm_polisher = None
        self._llm_polish_fail_notified = False


class _EnhancerHost(_EnhancementStepsMixin):
    """Minimal mixin host: only the attributes the steps read."""

    # Defined on the pipeline (orchestrator), not the mixin; the bare
    # host needs it for ``_call_polish_with_timeout``.
    _LLM_POLISH_PIPELINE_TIMEOUT_S = 4.0

    def __init__(self, app: _FakeApp) -> None:
        self._app = app
        self._templates_applied = False
        self._cycle_id = "test-cycle"


def _make_host(**config_updates: object) -> _EnhancerHost:
    config = types.SimpleNamespace(
        llm_polish=False,
        llm_api_key="",
        openai_api_key="",
        llm_polish_consent=False,
        llm_api_url=None,
        llm_model=None,
        llm_preset=None,
        ai_enhancement_enabled=True,
    )
    for key, value in config_updates.items():
        setattr(config, key, value)
    return _EnhancerHost(_FakeApp(config))


class _EventSpy:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)


def _raising_enhancer(text: str, config: object) -> str:
    raise RuntimeError("rule boom")


class _RaisingPolisher:
    def polish(self, text: str) -> str:
        raise ValueError("provider down")


@pytest.fixture
def event_spy():
    spy = _EventSpy()
    event_bus.subscribe(spy)
    yield spy
    event_bus.unsubscribe(spy)


class TestAiEnhancementFailurePath:
    """Step 7b (rule-based): failure must degrade, never abort."""

    def test_failure_returns_original_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        host = _make_host()
        monkeypatch.setattr(
            "voice_typer.server.ai_enhancement.enhance_transcription",
            _raising_enhancer,
        )
        assert host._apply_ai_enhancement("hello world") == "hello world"

    def test_failure_publishes_enhancement_event_not_llm_event(
        self, monkeypatch: pytest.MonkeyPatch, event_spy: _EventSpy
    ) -> None:
        host = _make_host()
        monkeypatch.setattr(
            "voice_typer.server.ai_enhancement.enhance_transcription",
            _raising_enhancer,
        )
        result = host._apply_ai_enhancement("hello world")
        assert result == "hello world"
        published = [e["type"] for e in event_spy.events]
        assert "text_enhancement_failed" in published
        # The E9-class mismatch: a rule-based failure must NOT be
        # reported under the LLM-polish event name (that would surface
        # the wrong toast in the renderer).
        assert "llm_polish_failed" not in published

    def test_failure_survives_raising_event_bus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A raising event bus must not abort the dictation (suppress-wrap)."""
        host = _make_host()
        monkeypatch.setattr(
            "voice_typer.server.ai_enhancement.enhance_transcription",
            _raising_enhancer,
        )

        def _raising_publish(event: dict) -> bool:
            raise RuntimeError("bus broken")

        monkeypatch.setattr("voice_typer.server.event_bus.publish", _raising_publish)
        assert host._apply_ai_enhancement("hello world") == "hello world"

    def test_success_publishes_nothing(self, monkeypatch: pytest.MonkeyPatch, event_spy: _EventSpy) -> None:
        host = _make_host()
        monkeypatch.setattr(
            "voice_typer.server.ai_enhancement.enhance_transcription",
            lambda text, config: text.upper(),
        )
        assert host._apply_ai_enhancement("hello") == "HELLO"
        assert event_spy.events == []


class TestLlmPolishFailurePath:
    """Step 7 (LLM): failure keeps its own event name + notify-once."""

    def test_failure_publishes_llm_event_and_notifies_once(self, event_spy: _EventSpy) -> None:
        host = _make_host(llm_polish=True, llm_api_key="sk-test", llm_polish_consent=True)
        host._app._llm_polisher = _RaisingPolisher()
        result1 = host._apply_llm_polish("hello")
        result2 = host._apply_llm_polish("world")
        assert result1 == "hello"
        assert result2 == "world"
        published = [e["type"] for e in event_spy.events]
        assert published == ["llm_polish_failed", "llm_polish_failed"]
        # notify-once per session: two failures → one tray notification.
        assert len(host._app.tray.notifications) == 1
        assert "LLM polish failed" in host._app.tray.notifications[0][1]

    def test_polish_without_consent_publishes_consent_required(self, event_spy: _EventSpy) -> None:
        host = _make_host(llm_polish=True, llm_api_key="sk-test", llm_polish_consent=False)
        host._app._llm_polisher = _RaisingPolisher()
        result = host._apply_llm_polish("hello")
        assert result == "hello"
        published = [e["type"] for e in event_spy.events]
        assert published == ["consent_required"]
