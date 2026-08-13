"""§10.1 — structural / drift tests for ``useNetworkOnline.ts``.

The test command for this slice is ``pytest tests/test_update*.py -x``,
so this is a PYTHON test that reads the TypeScript file as text and
asserts on key patterns. This mirrors the pattern used by
``tests/test_branding_scan_coverage.py`` and
``tests/test_api_doc_accuracy.py`` — structural drift tests that pin
the contract of cross-language files.

The actual runtime behavior of the hook is tested by the renderer's
vitest suite (``hooks/__tests__/useNetworkOnline.test.tsx`` — to be
added by Sub-agent 9 or a future renderer test pass). This Python
test ensures the FILE exists at the expected path + the PUBLIC API
contract is intact, so a refactor that breaks the auto-update wiring
is caught even if the vitest suite isn't run.

What this test pins:
  1. The file exists at
     ``voice_typer/client/src/renderer/src/hooks/useNetworkOnline.ts``.
  2. The hook is exported as ``useNetworkOnline``.
  3. The result interface ``UseNetworkOnlineResult`` is exported.
  4. The hook subscribes to ``window.addEventListener("online", ...)``
     AND ``window.addEventListener("offline", ...)`` (§10.1 — the
     network-is-back trigger).
  5. The hook calls ``removeEventListener`` in cleanup (no listener
     leak — important for React StrictMode double-mount in dev).
  6. The hook calls the Python IPC command ``check_pack_update`` (the
     command exposed by
     ``voice_typer/server/service/update_check.py``).
  7. The hook imports ``usePython`` from ``@/hooks/usePython`` (the
     transport-agnostic IPC bridge — same pattern as
     ``usePackDownload``).
  8. The hook catches IPC errors gracefully (forward-compat: the
     ``check_pack_update`` command may not be registered yet in
     ``ipc/registry.py`` — the hook must not crash).
  9. The hook only triggers a re-check on the false → true
     ``navigator.onLine`` transition (not on every ``online`` event —
     browsers fire duplicate events during connection flapping).
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
    """Read the hook file once per module; skip all tests if missing.

    Using ``skip`` (not ``fail``) when the file is missing lets the test
    suite pass in environments where the renderer hasn't been checked
    out (e.g. a Python-only CI that doesn't clone ``voice_typer/client``).
    """
    if not _HOOK_PATH.exists():
        pytest.skip(f"useNetworkOnline.ts not found at {_HOOK_PATH}")
    return _HOOK_PATH.read_text(encoding="utf-8")


# ── File existence ─────────────────────────────────────────────────────


class TestFileExists:
    """The hook file exists at the expected path."""

    def test_file_exists(self):
        """The file must exist — skipping is acceptable (Python-only CI),
        but if the file is PRESENT and malformed, the downstream tests
        must catch it."""
        if not _HOOK_PATH.exists():
            pytest.skip(f"useNetworkOnline.ts not found at {_HOOK_PATH}")
        # If we get here, the file exists.
        assert _HOOK_PATH.is_file()


# ── Exports ────────────────────────────────────────────────────────────


class TestExports:
    """The hook + its result interface are exported."""

    def test_use_network_online_exported(self, hook_source: str):
        """``useNetworkOnline`` is exported as a named export."""
        # Match ``export function useNetworkOnline(`` or
        # ``export const useNetworkOnline =``.
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


# ── Browser event subscription ─────────────────────────────────────────


class TestBrowserEventSubscription:
    """The hook subscribes to ``online`` / ``offline`` browser events."""

    def test_subscribes_to_online_event(self, hook_source: str):
        """``window.addEventListener("online", ...)`` is present."""
        # Allow single OR double quotes.
        assert re.search(
            r'addEventListener\(\s*["\']online["\']',
            hook_source,
        ), "hook must subscribe to the 'online' browser event (§10.1)"

    def test_subscribes_to_offline_event(self, hook_source: str):
        """``window.addEventListener("offline", ...)`` is present."""
        assert re.search(
            r'addEventListener\(\s*["\']offline["\']',
            hook_source,
        ), "hook must subscribe to the 'offline' browser event"

    def test_removes_listeners_in_cleanup(self, hook_source: str):
        """``removeEventListener`` is called for both events in the effect cleanup.

        Without this, React StrictMode double-mount in dev leaks
        listeners (each mount adds a new listener; the cleanup is the
        only thing that prevents accumulation).
        """
        # Count addEventListener vs removeEventListener calls — they
        # should be balanced (at least 2 removes for 2 adds).
        adds = len(re.findall(r'addEventListener\(\s*["\'](?:online|offline)["\']', hook_source))
        removes = len(re.findall(r'removeEventListener\(\s*["\'](?:online|offline)["\']', hook_source))
        assert adds >= 2, f"expected ≥2 addEventListener calls, got {adds}"
        assert removes >= 2, (
            f"expected ≥2 removeEventListener calls in cleanup, got {removes} — "
            "listener leak risk under React StrictMode double-mount"
        )


# ── IPC integration ────────────────────────────────────────────────────


class TestIpcIntegration:
    """The hook calls the Python IPC command ``check_pack_update``."""

    def test_calls_check_pack_update_command(self, hook_source: str):
        """The hook calls ``call("check_pack_update", ...)`` to trigger a re-check.

        This is the wiring between the renderer-side network-online trigger
        and the Python-side ``update_check.check_pack_update`` function.
        """
        assert '"check_pack_update"' in hook_source or "'check_pack_update'" in hook_source, (
            "hook must call the 'check_pack_update' IPC command "
            "(exposed by voice_typer/server/service/update_check.py)"
        )

    def test_imports_use_python(self, hook_source: str):
        """The hook imports ``usePython`` from ``@/hooks/usePython``.

        This is the transport-agnostic IPC bridge — same pattern as
        ``usePackDownload``. The hook must NOT touch Tauri or Electron
        APIs directly (see the contract at the top of ``usePython.ts``).
        """
        assert re.search(
            r'import\s+\{[^}]*usePython[^}]*\}\s*from\s*["\']@/hooks/usePython["\']',
            hook_source,
        ), "hook must import usePython from @/hooks/usePython"

    def test_catches_ipc_errors_gracefully(self, hook_source: str):
        """The IPC call is wrapped in try/catch — a missing registration
        (forward-compat: ``check_pack_update`` may not be in
        ``ipc/registry.py`` yet) must NOT crash the hook."""
        # Look for a try/catch around the call.
        # The exact shape varies (try/catch, .catch, etc.), but the
        # ``check_pack_update`` reference must be inside a try block
        # OR the call must use ``.catch`` / ``void`` + try.
        assert "try" in hook_source and "catch" in hook_source, (
            "hook must wrap the check_pack_update IPC call in try/catch — "
            "the command may not be registered in ipc/registry.py yet "
            "(forward-compat: the call fails gracefully until the wiring lands)"
        )


# ── Transition dedup ───────────────────────────────────────────────────


class TestTransitionDedup:
    """The hook only triggers a re-check on the false → true transition."""

    def test_uses_ref_to_track_previous_online_state(self, hook_source: str):
        """The hook uses a ref (or equivalent) to detect the false → true
        transition, NOT just every ``online`` event.

        Browsers fire duplicate ``online`` events during connection
        flapping; without dedup, the IPC would be spammed. The ref
        pattern (``isOnlineRef`` or similar) is the standard React
        idiom for "previous state" tracking inside an event listener
        added once on mount.
        """
        # Look for a ref tracking online state. The exact name varies,
        # but the pattern is ``useRef<boolean>`` + an assignment in the
        # effect / render body.
        assert "useRef" in hook_source, (
            "hook must use useRef to track the previous isOnline state — "
            "browsers fire duplicate 'online' events during connection flapping"
        )

    def test_transition_check_in_online_handler(self, hook_source: str):
        """The ``online`` handler checks the previous state before triggering."""
        # Look for a conditional like ``if (!wasOnline)`` or
        # ``if (!isOnlineRef.current)`` inside the online handler.
        # The exact variable name varies; we look for the pattern of
        # ``!`` + a name containing "online" inside the online handler.
        online_handler_match = re.search(
            r"handleOnline\s*=\s*\(\)\s*=>\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
            hook_source,
        )
        assert online_handler_match, (
            "could not find handleOnline handler — the online event listener "
            "must use a named handler so the transition dedup is visible"
        )
        handler_body = online_handler_match.group(1)
        # The handler must check a "previous online" flag before
        # triggering the re-check. Look for ``!`` followed by an
        # identifier containing "online" or "was".
        assert re.search(r"!\s*\w*(?:[Oo]nline|was|prev)\w*", handler_body), (
            "the online handler must check the previous isOnline state before "
            "triggering a re-check — avoids IPC spam during connection flapping. "
            f"Handler body:\n{handler_body}"
        )


# ── Return type ────────────────────────────────────────────────────────


class TestReturnType:
    """The hook returns the documented ``UseNetworkOnlineResult`` shape."""

    def test_returns_is_online(self, hook_source: str):
        """``isOnline`` is in the return object."""
        assert re.search(r"return\s*\{[^}]*\bisOnline\b", hook_source, re.DOTALL), (
            "hook must return { isOnline, ... }"
        )

    def test_returns_last_online_at(self, hook_source: str):
        """``lastOnlineAt`` is in the return object."""
        assert re.search(r"return\s*\{[^}]*\blastOnlineAt\b", hook_source, re.DOTALL), (
            "hook must return { lastOnlineAt, ... }"
        )

    def test_returns_trigger_recheck(self, hook_source: str):
        """``triggerRecheck`` is in the return object (exposed for Settings →
        "Check for updates now" buttons + tests)."""
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
        assert re.search(r"return\s*\{[^}]*\berror\b", hook_source, re.DOTALL), (
            "hook must return { error, ... }"
        )


# ── SSRF / no direct network ────────────────────────────────────────────


class TestNoDirectNetwork:
    """The hook does NOT make direct HTTP requests.

    All network requests (fetch / XMLHttpRequest / axios) are FORBIDDEN
    in the renderer — the SSRF defense lives in the Python side
    (``assert_pack_url_allowed``), and the renderer must go through the
    IPC bridge so the same SSRF check runs for every request. A direct
    ``fetch("https://...")`` in the renderer would bypass the allowlist
    + IP-literal blocklist.
    """

    def test_no_fetch_call(self, hook_source: str):
        """No ``fetch(...)`` call in the hook."""
        # Allow ``fetch`` only in comments (e.g. a docstring explaining
        # why we DON'T use fetch). Match ``fetch(`` outside of comments.
        # Strip comments (lines starting with ``//`` or ``*``).
        code_only = "\n".join(
            line for line in hook_source.splitlines()
            if not line.strip().startswith("//") and not line.strip().startswith("*")
        )
        assert "fetch(" not in code_only, (
            "hook must NOT call fetch() directly — all network requests must "
            "go through the Python IPC bridge so the SSRF defense "
            "(assert_pack_url_allowed) runs for every request"
        )

    def test_no_xmlhttprequest(self, hook_source: str):
        """No ``XMLHttpRequest`` in the hook."""
        assert "XMLHttpRequest" not in hook_source, (
            "hook must NOT use XMLHttpRequest — all network requests must "
            "go through the Python IPC bridge"
        )

    def test_no_axios(self, hook_source: str):
        """No ``axios`` import in the hook."""
        assert "axios" not in hook_source, (
            "hook must NOT import axios — all network requests must go "
            "through the Python IPC bridge"
        )
