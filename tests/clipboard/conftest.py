"""Shared fixtures and collection-time mocks for the clipboard test suite.

Centralizes the ``sys.modules.setdefault`` block for ``pynput``,
``pynput.keyboard``, and ``pyperclip`` that was previously duplicated
(in slightly varying forms — either ``MagicMock()`` directly or via
local ``mock_pynput`` / ``mock_pynput_kb`` variables) across 17
clipboard test files at ``tests/`` top level
(``tests/test_clipboard.py``, ``tests/test_clipboard_borrow_restore.py``,
…, ``tests/test_perf_clipboard_cred_security_fixes.py``).

Mirrors the ``tests/server/conftest.py:79-83`` pattern for ``pystray``:
a single collection-time ``sys.modules.setdefault("pynput", MagicMock())``
block bridges the window between pytest's initial collection pass and
the autouse ``mock_heavy_imports`` fixture in ``tests/conftest.py``
(which installs a per-test pynput mock at function scope, after every
test module's top-level ``from voice_typer.server.clipboard import …``
line has already executed).

Why a separate ``tests/clipboard/`` conftest (and not just inline in
``tests/conftest.py``)?

- **Single source of truth.** Future clipboard tests that live under
  ``tests/clipboard/`` (the long-term migration target for the 17
  top-level ``test_clipboard_*.py`` files) pick this up automatically
  by virtue of pytest's per-directory conftest collection. Today's
  top-level clipboard tests still rely on the per-test
  ``mock_heavy_imports`` fixture plus the fact that
  ``voice_typer.server.clipboard`` lazy-imports ``pynput`` (only
  ``pyperclip`` is imported at module load, and pyperclip is already
  mocked session-wide by ``mock_heavy_imports_session`` in
  ``tests/conftest.py``); removing their inline ``setdefault`` blocks
  is therefore safe — see the verification block at the bottom of this
  file's docstring.

- **No pollution of sibling test suites.** Keeping the pynput setdefault
  scoped to ``tests/clipboard/`` (instead of promoting it to
  ``tests/conftest.py``) means hotkey tests that genuinely need real
  pynput (e.g. ``tests/test_hotkeys.py`` with ``@pytest.mark.real_pynput``)
  are not affected. The parent ``tests/conftest.py`` deliberately
  installs pynput mocks per-test (not at collection time) precisely so
  ``real_pynput`` can evict them; promoting the setdefault to the
  parent would defeat that eviction.

- **Mirror of the ``tests/server/conftest.py`` convention.** That file
  documents (lines 38-83) the rationale for a collection-time
  ``sys.modules.setdefault("pystray", …)`` block, including the
  defensive safety net even when the production import chain is
  lazy. This file follows the same convention for the clipboard
  package's heavy imports.

Note: ``PIL`` is intentionally NOT mocked here (same rationale as the
``tests/server/conftest.py`` block — see that file's comment block).
Tests that need real PIL (e.g. ``tests/test_tray_icon.py`` with
``@pytest.mark.real_pil``) would break if we polluted ``sys.modules``
at collection time.
"""

import sys
from unittest.mock import MagicMock

# ── Collection-time pynput / pyperclip mock ─────────────────────────────
#
# These three ``setdefault`` calls run when this conftest.py is collected
# (before any test function executes). They install MagicMock stand-ins
# for ``pynput``, ``pynput.keyboard``, and ``pyperclip`` so that any
# module imported during collection whose import chain pulls in
# ``voice_typer.server.clipboard`` resolves to mocks instead of the real
# (potentially X-server-requiring) pynput. The ``setdefault`` (not
# ``setitem``) ensures we don't clobber a real pynput install when one
# is genuinely available (e.g. on a dev macOS box with a GUI session).
#
# On Linux CI headless sandboxes, ``import pynput`` raises
# ``ImportError: this platform is not supported: ('failed to acquire X
# connection: Bad display name ""', …)`` at the ``pynput.keyboard``
# backend import — so without this setdefault, ANY test module that
# imports ``voice_typer.server.clipboard`` at module load time would
# crash at collection time before the autouse ``mock_heavy_imports``
# fixture could install the mock.
#
# The autouse ``mock_heavy_imports`` fixture in ``tests/conftest.py``
# re-installs these mocks per-test (with fresh ``MagicMock()`` instances
# each time) so test-to-test isolation is preserved; this collection-
# time setdefault only bridges the pre-fixture window.
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())
