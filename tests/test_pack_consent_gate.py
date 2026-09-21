"""Pack consent gate: always-on (user product decision, no disable path)."""

from __future__ import annotations

from types import SimpleNamespace

from voice_typer.server.service import offline_pack


def _config(*, offline_pack_consent: bool = False, huggingface_consent: bool = False):
    return SimpleNamespace(
        offline_pack_consent=offline_pack_consent,
        huggingface_consent=huggingface_consent,
    )


class TestConsentGateAlwaysOn:
    """Pack downloads never blocked by consent state."""

    def test_no_config_always_allows(self):
        offline_pack.require_offline_pack_consent(None, version="v1")

    def test_consent_false_still_allows(self):
        offline_pack.require_offline_pack_consent(_config(offline_pack_consent=False), version="v1")

    def test_consent_true_allows(self):
        offline_pack.require_offline_pack_consent(_config(offline_pack_consent=True), version="v1")

    def test_huggingface_consent_irrelevant(self):
        offline_pack.require_offline_pack_consent(
            _config(offline_pack_consent=False, huggingface_consent=True),
            version="v1",
        )

    def test_never_raises_consent_error(self):
        try:
            offline_pack.require_offline_pack_consent(_config(offline_pack_consent=False), version="v2.3.1")
        except offline_pack.OfflinePackConsentRequiredError as exc:  # pragma: no cover
            raise AssertionError("require_offline_pack_consent must never raise (always-on)") from exc


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-x"])
