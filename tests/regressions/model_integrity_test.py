"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch


# WP-1: the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestModelIntegrityWarnsOnEmptyHashes:
    """SEC-audit-005.

    The finding: all 6 entries in model_hashes.json have empty ``files``
    dicts, so SHA-256 verification never runs. The fix: emit a WARNING
    (not just INFO) so operators notice the no-op state at default log
    levels.

    Tests pin:
    - ``verify_model_integrity`` logs a WARNING containing "NO-OP"
      when the manifest's ``files`` dict is empty.
    - ``_verify_qwen_model_hashes`` (qwen_engine) does the same.
    """

    def test_security_logs_warning_when_files_empty(self, tmp_path, caplog):
        from voice_typer.server import security
        from voice_typer.server.security import verify_model_integrity

        # Create a fake model directory with a model file (safetensors)
        # so the structural checks pass and we reach the empty-files branch.
        model_dir = tmp_path / "fake-model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_bytes(b"fake")

        # Patch MODEL_HASHES to return an empty files dict for our repo
        fake_manifest = {"fake/repo": {"revision": "main", "files": {}}}
        with patch.object(security, "MODEL_HASHES", fake_manifest), caplog.at_level(logging.WARNING):
            result = verify_model_integrity(local_dir=str(model_dir), repo_id="fake/repo")

        # Soft pass (structural checks pass, hash check is a no-op)
        assert result is True
        # Must have emitted a WARNING with "NO-OP"
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("NO-OP" in r.getMessage() for r in warnings), (
            "verify_model_integrity must emit a WARNING containing 'NO-OP' "
            "when the manifest's files dict is empty, so operators notice "
            "the integrity check is effectively disabled."
        )

    def test_qwen_logs_warning_when_files_empty(self, tmp_path, caplog):
        from voice_typer.server import security
        from voice_typer.server.qwen_engine import _verify_qwen_model_hashes

        # Create a fake qwen model dir with a config.json
        model_dir = tmp_path / "fake-qwen"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")

        # Patch MODEL_HASHES to return an empty files dict for qwen
        with patch.object(security, "MODEL_HASHES", {"qwen": {"files": {}}}), caplog.at_level(logging.WARNING):
            result = _verify_qwen_model_hashes(str(model_dir))

        assert result is True  # soft pass
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("NO-OP" in r.getMessage() for r in warnings), (
            "_verify_qwen_model_hashes must emit a WARNING containing 'NO-OP' "
            "when the qwen manifest's files dict is empty."
        )

    def test_model_hashes_json_currently_populated(self):
        """SEC-audit-005 (Round 0 fix): HuggingFace repo entries in
        model_hashes.json now have POPULATED ``files`` dicts (at minimum
        config.json pinned with a SHA-256 hash) and pinned 40-char commit
        SHA revisions (NOT the mutable 'main' branch).

        Pre-fix state (pinned by the previous version of this test): every
        entry had ``revision: "main"`` and ``files: {}``, so
        verify_model_integrity() was a no-op that logged a WARNING and
        soft-passed. This left Voice Typer open to supply-chain attacks
        where a compromised HuggingFace repo pushes a malicious new commit
        to 'main'.

        Post-fix state (this test): every HuggingFace repo has an immutable
        commit SHA and at least config.json pinned. Only the 'qwen' entry
        (a local model, not fetched from HuggingFace) retains an empty
        files dict by design.

        Detailed enforcement (hash mismatch, fallback matches JSON, etc.)
        is covered by tests/test_model_integrity.py.
        """
        import re

        commit_sha_re = re.compile(r"^[0-9a-f]{40}$")
        sha256_re = re.compile(r"^[0-9a-f]{64}$")

        manifest_path = Path(__file__).resolve().parent.parent.parent / "voice_typer" / "server" / "model_hashes.json"
        with open(manifest_path) as f:
            data = json.load(f)

        hf_repos_found = 0
        for repo_id, entry in data.items():
            if repo_id.startswith("_"):
                continue  # skip _comment and other metadata keys
            assert isinstance(entry, dict), (
                f"model_hashes.json entry {repo_id!r} must be a dict, got {type(entry).__name__}"
            )
            assert "revision" in entry, f"model_hashes.json entry {repo_id!r} must have a 'revision' key"
            assert "files" in entry, f"model_hashes.json entry {repo_id!r} must have a 'files' key"

            if repo_id == "qwen":
                assert entry["revision"] == "local", f"qwen entry revision should be 'local', got {entry['revision']!r}"
                continue

            hf_repos_found += 1
            assert commit_sha_re.match(entry["revision"]), (
                f"model_hashes.json entry {repo_id!r} revision {entry['revision']!r} "
                f"is not a 40-char hex commit SHA — must not be 'main' or any "
                f"mutable branch (supply-chain attack surface)."
            )
            assert entry["files"], (
                f"model_hashes.json entry {repo_id!r} has empty 'files' dict — "
                f"verify_model_integrity() is a no-op for this repo. "
                f"Pin at least config.json."
            )
            assert "config.json" in entry["files"], f"model_hashes.json entry {repo_id!r} must pin config.json"
            assert sha256_re.match(entry["files"]["config.json"]), (
                f"model_hashes.json entry {repo_id!r} config.json hash "
                f"{entry['files']['config.json']!r} is not a 64-char hex SHA-256."
            )

        assert hf_repos_found >= 5, f"Expected at least 5 HuggingFace repos with pinned SHAs, found {hf_repos_found}"
