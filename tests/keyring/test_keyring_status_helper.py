"""Domain: credential store / keyring, the ``_keyring_status()``"""

from __future__ import annotations


class TestKeyringStatusHelper:
    """SVC-6: ``_keyring_status()`` centralizes the duplicated probe."""

    def test_returns_dict_with_expected_keys(self, tmp_config_dir, monkeypatch):
        """``_keyring_status`` returns a dict containing the four"""
        from voice_typer.server.service import LausuService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = LausuService(FakeApp())
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
        """helper returns a safe ``{available: False, fallback: True, ...}``"""
        from voice_typer.server.service import LausuService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = LausuService(FakeApp())

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
        """Both ``get_config`` and ``get_defaults`` route through"""
        from voice_typer.server.service import LausuService

        calls: list[int] = []

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = LausuService(FakeApp())

        def _spy(self):
            calls.append(1)
            return {"available": False, "backend": None, "fallback": True, "reason": "spy"}

        monkeypatch.setattr(LausuService, "_keyring_status", _spy)

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
