"""
§10.1: structural / drift tests for ``useNetworkOnline.ts``.
``ipc/registry.py``, the hook must not crash).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Path to the renderer hook file (relative to the repo root).
_HOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "hooks"
    / "useNetworkOnline.ts"
)


@pytest.fixture(scope="module")
def hook_source() -> str:
    """Read the hook file once per module; skip all tests if missing."""
    if not _HOOK_PATH.exists():
        pytest.skip(f"useNetworkOnline.ts not found at {_HOOK_PATH}")
    return _HOOK_PATH.read_text(encoding="utf-8")


class TestFileExists:
    """The hook file exists at the expected path."""

    def test_file_exists(self):
        """The file must exist, skipping is acceptable (Python-only CI),"""
        if not _HOOK_PATH.exists():
            pytest.skip(f"useNetworkOnline.ts not found at {_HOOK_PATH}")
        # If we get here, the file exists.
        assert _HOOK_PATH.is_file()


class TestExports:
    """The hook + its result interface are exported."""

    def test_use_network_online_exported(self, hook_source: str):
        """``useNetworkOnline`` is exported as a named export."""
        assert re.search(
            r"export\s+(?:function|const)\s+useNetworkOnline\b",
            hook_source,
        ), "useNetworkOnline must be exported as a named export"

    def test_result_interface_exported(self, hook_source: str):
        """``UseNetworkOnlineResult`` interface is exported (for consumer type-safety)."""
        assert re.search(
            r"export\s+interface\s+UseNetworkOnlineResult\b",
            hook_source,
        ), "UseNetworkOnlineResult interface must be exported"


class TestBrowserEventSubscription:
    """The hook subscribes to ``online`` / ``offline`` browser events."""

    def test_subscribes_to_online_event(self, hook_source: str):
        """``window.addEventListener(\"online\", ...)`` is present."""
        # Allow single OR double quotes.
        assert re.search(
            r'addEventListener\(\s*["\']online["\']',
            hook_source,
        ), "hook must subscribe to the 'online' browser event (§10.1)"

    def test_subscribes_to_offline_event(self, hook_source: str):
        """``window.addEventListener(\"offline\", ...)`` is present."""
        assert re.search(
            r'addEventListener\(\s*["\']offline["\']',
            hook_source,
        ), "hook must subscribe to the 'offline' browser event"

    def test_removes_listeners_in_cleanup(self, hook_source: str):
        """``removeEventListener`` is called for both events in the effect cleanup."""
        # Count addEventListener vs removeEventListener calls, they
        adds = len(re.findall(r'addEventListener\(\s*["\'](?:online|offline)["\']', hook_source))
        removes = len(re.findall(r'removeEventListener\(\s*["\'](?:online|offline)["\']', hook_source))
        assert adds >= 2, f"expected ≥2 addEventListener calls, got {adds}"
        assert removes >= 2, (
            f"expected ≥2 removeEventListener calls in cleanup, got {removes}, "
            "listener leak risk under React StrictMode double-mount"
        )


class TestIpcIntegration:
    """The hook calls the Python IPC command ``check_offline_pack_update``."""

    def test_calls_check_offline_pack_update_command(self, hook_source: str):
        """The hook calls ``call(\"check_offline_pack_update\", ...)`` to trigger a re-check."""
        assert '"check_offline_pack_update"' in hook_source or "'check_offline_pack_update'" in hook_source, (
            "hook must call the 'check_offline_pack_update' IPC command "
            "(exposed by voice_typer/server/service/update_check.py)"
        )

    def test_imports_use_python(self, hook_source: str):
        """
        The hook imports ``usePython`` from ``@/hooks/usePython``.
        ``useOfflinePackDownload``. The hook must NOT touch Tauri or predecessor
        APIs directly (see the contract at the top of ``usePython.ts``).
        """
        assert re.search(
            r'import\s+\{[^}]*usePython[^}]*\}\s*from\s*["\']@/hooks/usePython["\']',
            hook_source,
        ), "hook must import usePython from @/hooks/usePython"

    def test_catches_ipc_errors_gracefully(self, hook_source: str):
        """
        The IPC call is wrapped in try/catch, a missing registration
        ``ipc/registry.py`` yet) must NOT crash the hook.
        """
        # Look for a try/catch around the call.
        assert "try" in hook_source and "catch" in hook_source, (
            "hook must wrap the check_offline_pack_update IPC call in try/catch, "
            "the command may not be registered in ipc/registry.py yet "
            "(forward-compat: the call fails gracefully until the wiring lands)"
        )


class TestTransitionDedup:
    """The hook only triggers a re-check on the false → true transition."""

    def test_uses_ref_to_track_previous_online_state(self, hook_source: str):
        """The hook uses a ref (or equivalent) to detect the false → true"""
        # Look for a ref tracking online state. The exact name varies,
        assert "useRef" in hook_source, (
            "hook must use useRef to track the previous isOnline state, "
            "browsers fire duplicate 'online' events during connection flapping"
        )

    def test_transition_check_in_online_handler(self, hook_source: str):
        """The ``online`` handler checks the previous state before triggering."""
        online_handler_match = re.search(
            r"handleOnline\s*=\s*\(\)\s*=>\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
            hook_source,
        )
        assert online_handler_match, (
            "could not find handleOnline handler, the online event listener "
            "must use a named handler so the transition dedup is visible"
        )
        handler_body = online_handler_match.group(1)
        # The handler must check a "previous online" flag before
        assert re.search(r"!\s*\w*(?:[Oo]nline|was|prev)\w*", handler_body), (
            "the online handler must check the previous isOnline state before "
            "triggering a re-check, avoids IPC spam during connection flapping. "
            f"Handler body:\n{handler_body}"
        )


class TestReturnType:
    """The hook returns the documented ``UseNetworkOnlineResult`` shape."""

    def test_returns_is_online(self, hook_source: str):
        """``isOnline`` is in the return object."""
        assert re.search(r"return\s*\{[^}]*\bisOnline\b", hook_source, re.DOTALL), "hook must return { isOnline, ... }"

    def test_returns_last_online_at(self, hook_source: str):
        """``lastOnlineAt`` is in the return object."""
        assert re.search(r"return\s*\{[^}]*\blastOnlineAt\b", hook_source, re.DOTALL), (
            "hook must return { lastOnlineAt, ... }"
        )

    def test_returns_trigger_recheck(self, hook_source: str):
        """``triggerRecheck`` is in the return object (exposed for Settings →"""
        assert re.search(r"return\s*\{[^}]*\btriggerRecheck\b", hook_source, re.DOTALL), (
            "hook must return { triggerRecheck, ... }"
        )

    def test_returns_is_checking(self, hook_source: str):
        """``isChecking`` is in the return object (for spinner / button disable)."""
        assert re.search(r"return\s*\{[^}]*\bisChecking\b", hook_source, re.DOTALL), (
            "hook must return { isChecking, ... }"
        )

    def test_returns_error(self, hook_source: str):
        """``error`` is in the return object (last IPC error, or null)."""
        assert re.search(r"return\s*\{[^}]*\berror\b", hook_source, re.DOTALL), "hook must return { error, ... }"


class TestNoDirectNetwork:
    """
    The hook does NOT make direct HTTP requests.
    All network requests (fetch / XMLHttpRequest / axios) are FORBIDDEN
    """

    def test_no_fetch_call(self, hook_source: str):
        """No ``fetch(...)`` call in the hook."""
        # Allow ``fetch`` only in comments (e.g. a docstring explaining
        code_only = "\n".join(
            line
            for line in hook_source.splitlines()
            if not line.strip().startswith("//") and not line.strip().startswith("*")
        )
        assert "fetch(" not in code_only, (
            "hook must NOT call fetch() directly, all network requests must "
            "go through the Python IPC bridge so the SSRF defense "
            "(assert_offline_pack_url_allowed) runs for every request"
        )

    def test_no_xmlhttprequest(self, hook_source: str):
        """No ``XMLHttpRequest`` in the hook."""
        assert "XMLHttpRequest" not in hook_source, (
            "hook must NOT use XMLHttpRequest, all network requests must go through the Python IPC bridge"
        )

    def test_no_axios(self, hook_source: str):
        """No ``axios`` import in the hook."""
        assert "axios" not in hook_source, (
            "hook must NOT import axios, all network requests must go through the Python IPC bridge"
        )
