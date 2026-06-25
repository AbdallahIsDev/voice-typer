"""Round 2 regression tests for the high-priority bug fixes.

Covers:
  - NEW-PRIV-005: TranscriptionEngine self.config crash (separate file
    test_round2_new_priv_005.py — 6 tests, all passing on the real
    construction path).
  - NEW-UX-035: Settings updateConfig now shows a "Saved" toast on success.
  - NEW-TS-ERR-NEW-UX-029: sound_feedback_enabled + consent fields
    exist in the TypeScript VoiceTyperConfig type.
  - NEW-PRIV-006: Models.tsx has per-provider consent toggles +
    setCloudConsent handler.
  - NEW-PRIV-009: About.tsx cites GDPR Article 9; Settings has a
    voice_biometric_consent toggle.
  - tray.py: Wayland early-return path uses _bg_thread (not _bg_work_thread).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDERER_SRC = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


# ── NEW-UX-035: updateConfig shows success toast ─────────────────────


class TestNewUx035SuccessToast:
    """The Settings updateConfig callback must call showSnack on success,
    not just on error.  Previously the success path was a comment-only
    intent with no actual call."""

    def test_update_config_calls_show_snack_on_success(self):
        settings = _read("pages/Settings.tsx")
        # The success path must actually call showSnack (not just
        # describe it in a comment).
        # Find the updateConfig callback body.
        assert "await call('set_config', updates)" in settings
        # After the await, there must be a showSnack call with 'success'.
        # We look for the literal call (not the comment).
        # Strip comments to avoid matching the old comment.
        lines = settings.splitlines()
        in_update_config = False
        success_toast_found = False
        for line in lines:
            stripped = line.strip()
            if "const updateConfig = useCallback" in stripped:
                in_update_config = True
            elif in_update_config and stripped.startswith("),") or (
                in_update_config and stripped.startswith("}, [")
            ):
                in_update_config = False
            elif in_update_config and stripped.startswith("//"):
                continue  # skip comment lines
            elif in_update_config and "showSnack(" in stripped and "success" in stripped:
                success_toast_found = True
                break
        assert success_toast_found, (
            "updateConfig must call showSnack(..., 'success') after a "
            "successful set_config call.  Only the error path had a toast "
            "before this fix."
        )

    def test_update_config_still_has_error_toast(self):
        """Regression guard: the error toast must still be there."""
        settings = _read("pages/Settings.tsx")
        assert "showSnack('Failed to save setting', 'error')" in settings


# ── NEW-TS-ERR-NEW-UX-029: TS config type completeness ───────────────


class TestNewTsErrNewUx029TypeCompleteness:
    """The VoiceTyperConfig TypeScript interface must include all
    config fields referenced from the renderer.  Previously
    sound_feedback_enabled + the consent fields were missing, causing
    TS2339 errors in IDE diagnostics (even though tsc --noEmit
    silently passed due to skipLibCheck)."""

    def test_sound_feedback_enabled_in_type(self):
        config_ts = _read("types/config.ts")
        assert "sound_feedback_enabled" in config_ts, (
            "VoiceTyperConfig must declare sound_feedback_enabled"
        )

    def test_huggingface_consent_in_type(self):
        config_ts = _read("types/config.ts")
        assert "huggingface_consent" in config_ts

    def test_cloud_consent_fields_in_type(self):
        config_ts = _read("types/config.ts")
        assert "cloud_openai_consent" in config_ts
        assert "cloud_groq_consent" in config_ts
        assert "cloud_deepgram_consent" in config_ts

    def test_voice_biometric_consent_in_type(self):
        config_ts = _read("types/config.ts")
        assert "voice_biometric_consent" in config_ts

    def test_llm_polish_consent_in_type(self):
        """Pre-existing field that was missing from the TS type —
        added in this round for the centralized Privacy & Consent
        section in Settings."""
        config_ts = _read("types/config.ts")
        assert "llm_polish_consent" in config_ts


# ── NEW-PRIV-006: Cloud ASR consent UI ───────────────────────────────


class TestNewPriv006CloudConsentUi:
    """Models.tsx must expose a consent toggle for each cloud provider
    so users can actually grant the consent the backend requires."""

    def test_models_imports_switch(self):
        models = _read("pages/Models.tsx")
        assert "import { Switch }" in models, (
            "Models.tsx must import Switch for the consent toggles"
        )

    def test_models_has_set_cloud_consent_handler(self):
        models = _read("pages/Models.tsx")
        assert "setCloudConsent" in models
        assert "cloud_openai_consent" in models
        assert "cloud_groq_consent" in models
        assert "cloud_deepgram_consent" in models

    def test_models_has_consent_key_helper(self):
        """The consentKeyFor helper exists to avoid ternary soup in JSX."""
        models = _read("pages/Models.tsx")
        assert "consentKeyFor" in models

    def test_models_has_consent_disclosure_text(self):
        """Each provider's consent section must explain what the user
        is agreeing to (audio sent to provider, provider's privacy
        policy applies)."""
        models = _read("pages/Models.tsx")
        assert "Audio transmission consent" in models
        # The "audio recordings will be sent" text spans a <strong> tag
        # in the JSX, so we check for the two halves separately.
        assert "audio recordings will be" in models
        assert "sent</strong>" in models
        assert "privacy policy applies" in models

    def test_models_has_hugging_face_consent_banner(self):
        """NEW-PRIV-005: a banner at the top of Models page explains
        HuggingFace consent and provides a grant button."""
        models = _read("pages/Models.tsx")
        assert "HuggingFace download consent required" in models
        assert "Grant consent" in models
        assert "setHuggingFaceConsent" in models

    def test_models_consent_section_only_shown_when_key_present(self):
        """The per-provider consent toggle should only render when the
        user has either entered an API key OR already granted consent
        (no point showing it otherwise)."""
        models = _read("pages/Models.tsx")
        # The conditional render uses apiKeys[provider.key] OR the consent flag
        assert "apiKeys[provider.key]" in models
        assert "consentKeyFor(provider.key)" in models


# ── NEW-PRIV-009: Voice biometric consent UI + GDPR Art. 9 ────────────


class TestNewPriv009VoiceBiometricUi:
    """About.tsx must cite GDPR Article 9 explicitly (not just 'other
    regulations').  Settings must have a centralized Privacy & Consent
    section with a voice_biometric_consent toggle."""

    def test_about_cites_gdpr_article_9(self):
        about = _read("pages/About.tsx")
        # Must cite GDPR Article 9 explicitly.
        assert "GDPR Article 9" in about, (
            "About.tsx must cite 'GDPR Article 9' (not just 'other regulations')"
        )
        # BIPA must still be there (regression guard).
        assert "BIPA" in about

    def test_settings_has_privacy_consent_section(self):
        settings = _read("pages/Settings.tsx")
        assert "Privacy & Consent" in settings
        assert "Review and revoke consent" in settings

    def test_settings_has_voice_biometric_consent_toggle(self):
        settings = _read("pages/Settings.tsx")
        assert "voice_biometric_consent" in settings
        assert "Voice biometric processing" in settings
        # The toggle's info text must cite BIPA + GDPR Art. 9.
        assert "BIPA" in settings
        assert "GDPR Article 9" in settings

    def test_settings_has_all_consent_toggles_consolidated(self):
        """The Privacy & Consent section must include all consent flags
        so the user has one place to review/revoke."""
        settings = _read("pages/Settings.tsx")
        assert "huggingface_consent" in settings
        assert "voice_biometric_consent" in settings
        assert "cloud_openai_consent" in settings
        assert "cloud_groq_consent" in settings
        assert "cloud_deepgram_consent" in settings
        assert "llm_polish_consent" in settings


# ── tray.py: thread attribute naming consistency ─────────────────────


class TestTrayThreadAttributeNaming:
    """MINOR FIX: Wayland early-return path must use self._bg_thread
    (canonical name), not self._bg_work_thread (which would orphan
    the thread if stop() ever joins it)."""

    def test_no_bg_work_thread_attribute(self):
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(
            encoding="utf-8"
        )
        # The buggy attribute name must NOT appear anywhere.
        assert "_bg_work_thread" not in tray_py, (
            "tray.py must not use _bg_work_thread — use _bg_thread (canonical name)"
        )

    def test_bg_thread_used_in_all_three_paths(self):
        """All three start() paths (Wayland, OSError, normal) must
        assign self._bg_thread so the attribute is consistent."""
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(
            encoding="utf-8"
        )
        # Count occurrences of "self._bg_thread = threading.Thread"
        count = tray_py.count("self._bg_thread = threading.Thread")
        assert count == 3, (
            f"Expected 3 assignments to self._bg_thread (Wayland path, "
            f"OSError path, normal path), got {count}"
        )
