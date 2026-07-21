"""Regression test for CR-39: diagnostic bundle must NOT include transcription text.

The "Export diagnostics" button (``CrashRecovery.create_diagnostic_bundle``)
packages a zip file that users routinely attach to bug reports.  Previously
the ``crash_recovery.json`` entry inside that zip dumped the full
``self._entries`` list verbatim — including the ``text`` field of every
recovery entry, i.e. the user's transcribed speech.

CR-39 fixes this by emitting only metadata (count + per-entry timestamp /
pasted flag / text_length) so support engineers can see *that* an entry
existed without exposing what was said.

These tests pin the redaction so a future refactor that re-introduces the
leak will fail loudly.
"""

from __future__ import annotations

import json
import zipfile

import pytest


@pytest.fixture
def recovery_dir(tmp_path, monkeypatch):
    """Point ``voice_typer.server.config._config_dir`` at a tmp_path.

    ``CrashRecovery.create_diagnostic_bundle`` looks up the config dir via
    ``_config_dir()`` (not the instance's ``self._path``) when deciding
    where to write the bundle zip — so we have to monkeypatch the global.
    The same pattern is used in ``tests/test_crash_recovery.py``.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


class TestDiagnosticBundleNoPII:
    """CR-39: the diagnostic bundle must NOT include transcription text."""

    def test_bundle_omits_transcription_text(self, recovery_dir):
        """create_diagnostic_bundle must NOT include the 'text' field of
        recovery entries in the bundle's ``crash_recovery.json``.

        Regression: pre-CR-39 the entry dict was dumped verbatim, leaking
        the user's transcribed speech (potentially containing passwords,
        medical info, etc.) into bug-report attachments.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        # Add recovery entries with sensitive text. If any of these
        # strings survive into the bundle, the test fails.
        cr.add("my secret password is hunter2")
        cr.add("patient John Doe has diabetes")

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None, "create_diagnostic_bundle must return a path, not None"

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            assert "crash_recovery.json" in names, "diagnostic bundle must include a crash_recovery.json entry"
            crash_recovery_json = zf.read("crash_recovery.json").decode("utf-8")

        data = json.loads(crash_recovery_json)

        # The metadata envelope must be present.
        assert data.get("count") == 2, f"expected count=2 in crash_recovery.json, got: {data!r}"
        entries = data.get("entries", [])
        assert len(entries) == 2, f"expected 2 metadata entries, got: {entries!r}"

        # Each entry must contain ONLY timestamp / pasted / text_length —
        # never the raw ``text`` field.  We also assert the sensitive
        # strings don't appear anywhere in the JSON (defence in depth
        # against a future field rename that re-introduces the leak).
        for entry in entries:
            assert "text" not in entry, f"'text' field present in diagnostic entry — PII leak: {entry!r}"
            assert "hunter2" not in json.dumps(entry), f"secret value 'hunter2' leaked into entry: {entry!r}"
            assert "John Doe" not in json.dumps(entry), f"PII 'John Doe' leaked into entry: {entry!r}"
            # Metadata fields must be present and well-typed.
            assert "timestamp" in entry, f"timestamp missing from entry: {entry!r}"
            assert "pasted" in entry, f"pasted flag missing from entry: {entry!r}"
            assert "text_length" in entry, f"text_length missing from entry: {entry!r}"
            assert isinstance(entry["text_length"], int)
            assert entry["text_length"] > 0, f"text_length must be a positive int (entry existed): {entry!r}"

        # Defence in depth: the sensitive strings must not appear anywhere
        # in the entire crash_recovery.json blob — even in a key name,
        # comment, or nested object.
        assert "hunter2" not in crash_recovery_json, "secret value 'hunter2' found in crash_recovery.json"
        assert "John Doe" not in crash_recovery_json, "PII 'John Doe' found in crash_recovery.json"
        assert "patient" not in crash_recovery_json.lower(), "transcription content leaked into crash_recovery.json"

    def test_bundle_omits_text_even_when_pasted(self, recovery_dir):
        """The redaction applies regardless of the ``pasted`` flag.

        Even pasted entries (which the user did successfully deliver to
        the target app) must not leak their text in the diagnostic
        bundle — the bundle is shared with third parties (bug reports),
        so the pasted flag doesn't change the privacy posture.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("pasted secret: ssn 123-45-6789", pasted=True)
        cr.add("unpasted secret: credit card 4111 1111 1111 1111", pasted=False)

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            crash_recovery_json = zf.read("crash_recovery.json").decode("utf-8")

        # No text, no SSN, no CC number — even though the pasted flag is
        # preserved in metadata.
        assert "123-45-6789" not in crash_recovery_json
        assert "4111 1111 1111 1111" not in crash_recovery_json
        assert "4111111111111111" not in crash_recovery_json

        data = json.loads(crash_recovery_json)
        # The pasted flag itself should still be present so support can
        # see the paste success rate.
        pasted_flags = [e.get("pasted") for e in data["entries"]]
        assert pasted_flags == [True, False], f"pasted flags not preserved in metadata: {pasted_flags!r}"

    def test_bundle_metadata_includes_count_and_text_length(self, recovery_dir):
        """The redacted metadata envelope must include ``count`` and
        per-entry ``text_length`` so support engineers can still see how
        many entries existed and how long each was.

        Without ``text_length``, the bundle would be useless for
        diagnosing "the user said they dictated a paragraph but the
        recovery buffer was empty" type bugs.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("short")
        cr.add("a much longer transcription than the first one")

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            data = json.loads(zf.read("crash_recovery.json"))

        assert data["count"] == 2
        lengths = [e["text_length"] for e in data["entries"]]
        # The first entry is "short" (5 chars), the second is the longer
        # string (46 chars including spaces).
        assert lengths == [5, 46], f"unexpected text_lengths: {lengths!r}"
        # The second entry is genuinely longer than the first — sanity
        # check that text_length isn't always 0 or some constant.
        assert lengths[1] > lengths[0]

    def test_bundle_has_no_text_key_anywhere_in_zip(self, recovery_dir):
        """Defence in depth: the literal ``"text":`` JSON key must not
        appear anywhere in the bundle's ``crash_recovery.json``.

        This catches a future regression where someone adds a new field
        like ``text_preview`` that re-introduces the leak under a
        different key.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("the quick brown fox jumps over the lazy dog")

        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            crash_recovery_json = zf.read("crash_recovery.json").decode("utf-8")

        # The JSON key ``"text"`` (with quotes) must not appear.
        assert '"text"' not in crash_recovery_json, (
            f"'text' JSON key found in crash_recovery.json — PII leak risk: {crash_recovery_json!r}"
        )
        # The transcribed phrase must not appear anywhere in the bundle
        # entry (would only happen if text was re-introduced).
        assert "quick brown fox" not in crash_recovery_json
