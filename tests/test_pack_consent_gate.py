"""§8.4: Consent gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from voice_typer.server.service import offline_pack


def _config(*, offline_pack_consent: bool = False, huggingface_consent: bool = False):
    """Build a minimal config-like object with both consent flags."""
    return SimpleNamespace(
        offline_pack_consent=offline_pack_consent,
        huggingface_consent=huggingface_consent,
    )


class TestConsentGate:
    """§8.4, pack download requires offline_pack_consent."""

    def test_no_config_raises(self):
        with pytest.raises(offline_pack.OfflinePackConsentRequiredError):
            offline_pack.require_offline_pack_consent(None, version="v1")

    def test_consent_false_raises(self):
        with pytest.raises(offline_pack.OfflinePackConsentRequiredError):
            offline_pack.require_offline_pack_consent(_config(offline_pack_consent=False), version="v1")

    def test_consent_true_passes(self):
        # Should not raise.
        offline_pack.require_offline_pack_consent(_config(offline_pack_consent=True), version="v1")

    def test_consent_field_is_offline_pack_consent(self):
        """The exception's structured field points at the right Settings toggle."""
        with pytest.raises(offline_pack.OfflinePackConsentRequiredError) as exc_info:
            offline_pack.require_offline_pack_consent(_config(), version="v1")
        assert exc_info.value.consent_field == "offline_pack_consent"

    def test_provider_is_github(self):
        """The download phones home to GitHub Releases, provider is github."""
        with pytest.raises(offline_pack.OfflinePackConsentRequiredError) as exc_info:
            offline_pack.require_offline_pack_consent(_config(), version="v1")
        assert exc_info.value.provider == "github"

    def test_huggingface_consent_alone_does_not_authorize(self):
        """``huggingface_consent=True`` MUST NOT authorize pack download."""
        with pytest.raises(offline_pack.OfflinePackConsentRequiredError):
            offline_pack.require_offline_pack_consent(
                _config(offline_pack_consent=False, huggingface_consent=True),
                version="v1",
            )

    def test_version_recorded_on_exception(self):
        with pytest.raises(offline_pack.OfflinePackConsentRequiredError) as exc_info:
            offline_pack.require_offline_pack_consent(_config(), version="v2.3.1")
        assert exc_info.value.version == "v2.3.1"


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
