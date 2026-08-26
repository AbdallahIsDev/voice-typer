"""Microphone refresh tests split out of the former ``tests/test_history_and_models.py``.

Domain: microphone listing — ``refresh_microphones(force=True)``
bypasses the 5 s TTL cache so callers that *know* a hot-plug event
happened can refresh immediately (SVC-8).

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
"""

from __future__ import annotations


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
            "voice_typer.server.server_platform.microphone_list.list_microphones",
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
