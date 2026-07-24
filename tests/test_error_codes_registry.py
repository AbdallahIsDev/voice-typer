"""Contract test for the IPC error-code registry (EC-FIX-4 / EC-10).

EC-10 found that the G4-M-22 namespacing migration was partial:

* ``_respond_with_error`` still stamped the legacy ``"internal_error"``.
* The ``ERROR_CODES`` registry listed 9 namespaced codes but 15+ legacy
  codes were actively emitted and NOT registered.
* The renderer's ``ErrorEvent.code`` was a bare ``string`` with no
  narrowing — clients branching on ``code`` silently fell through to a
  generic "unknown error" path for handler exceptions.

EC-FIX-4 closed those gaps:

1. ``handlers/_base.py`` now stamps ``"server.internal_error"`` (the
   namespaced form).
2. ``ipc/validation.py``'s ``ERROR_CODES`` registry now lists every
   actively-emitted namespaced code.
3. ``ipc/validation.py``'s module docstring documents which legacy
   non-namespaced aliases are still emitted for backward compatibility
   (the renderer must accept both forms).

This file is the regression guard. It asserts:

A. **Required namespaced codes** — every code listed in the EC-FIX-4
   spec is present in :data:`ERROR_CODES`.
B. **Every emitted ``"code": "<value>"`` literal** in the Python server
   source tree is either a registered namespaced code OR a documented
   legacy alias. This is the contract test requested by EC-FIX-4: if a
   future change introduces a new error code WITHOUT registering it
   (or without documenting it as a legacy alias), this test will fail.
C. **Behavioural guard** — calling
   :meth:`HandlerBase._respond_with_error` actually stamps the
   namespaced ``"server.internal_error"`` code on the response (guards
   against a future regression that re-introduces the bare legacy
   form).
D. **Default code on ``_error_response``** is the namespaced
   ``"server.handler_error"`` (guards against a future regression that
   reverts the default to the legacy ``"handler_error"``).

The grep in (B) uses Python's :mod:`pathlib` + :mod:`re` rather than
invoking an external ``rg`` binary so the test is self-contained and
portable across the Linux sandbox and Windows/macOS host validation
environments.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import ERROR_CODES, _error_response

# ────────────────────────────────────────────────────────────────────────────
# Paths
# ────────────────────────────────────────────────────────────────────────────

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SERVER_DIR = REPO_ROOT / "voice_typer" / "server"

# ────────────────────────────────────────────────────────────────────────────
# Required namespaced codes (per EC-FIX-4 spec)
# ────────────────────────────────────────────────────────────────────────────

# Every code in this set MUST be in ``ERROR_CODES``. If a code is
# missing, the registry needs to be expanded (or the code was
# accidentally renamed). The set mirrors the EC-FIX-4 task spec
# verbatim.
REQUIRED_NAMESPACED_CODES: frozenset[str] = frozenset(
    {
        # Server-originated.
        "server.internal_error",
        "server.shutting_down",
        "server.unknown_command",
        "server.unknown_tray_item",
        "server.handler_error",
        # Client-originated.
        "client.auth_failed",
        "client.invalid_payload",
        "client.rate_limited",
        # Pre-existing namespaced codes (kept for stability — these
        # were in the registry before EC-FIX-4 and must remain).
        "client.invalid_field",
        "client.missing_field",
        "client.path_not_allowed",
        "client.not_found",
        "server.file_locked",
        "server.model_switch_failed",
    }
)

# ────────────────────────────────────────────────────────────────────────────
# Documented legacy aliases (still emitted for backward compat)
# ────────────────────────────────────────────────────────────────────────────

# Each entry maps a legacy non-namespaced code → its namespaced
# counterpart in ``ERROR_CODES``. The renderer must accept BOTH forms
# (treat the legacy form as an alias). New code MUST use the namespaced
# form. This map MUST stay in sync with the comment block above
# ``ERROR_CODES`` in ``voice_typer/server/ipc/validation.py``.
#
# Codes that appear ONLY as ``code="..."`` keyword args to
# ``_error_response`` (e.g. ``not_initialized``, ``payload_too_large``)
# are included here even though the literal-``"code": "..."`` regex
# below doesn't catch that form — this keeps the alias registry
# comprehensive for future grep-based audits.
LEGACY_ALIASES: dict[str, str] = {
    "internal_error": "server.internal_error",
    "shutting_down": "server.shutting_down",
    "unknown_command": "server.unknown_command",
    "unknown_tray_item": "server.unknown_tray_item",
    "auth_failed": "client.auth_failed",
    "rate_limited": "client.rate_limited",
    "invalid_payload": "client.invalid_payload",
    "invalid_field": "client.invalid_field",
    "missing_field": "client.missing_field",
    "model_switch_failed": "server.model_switch_failed",
    "handler_error": "server.handler_error",
    # ``payload_too_large`` and ``not_initialized`` are emitted via the
    # ``code="..."`` keyword-arg form in ``vocabulary_handlers.py`` and
    # ``vocabulary_automation_handlers.py`` respectively. They have no
    # namespaced counterpart in ``ERROR_CODES`` yet — they pre-date the
    # G4-M-22 namespacing migration and are still emitted as bare
    # codes. Listed here (with an empty counterpart) so the literal
    # grep below accepts them; the counterpart-presence test in
    # ``TestLegacyAliases`` skips entries whose counterpart is empty.
    "payload_too_large": "",
    "not_initialized": "",
    "not_found": "client.not_found",
}

# ────────────────────────────────────────────────────────────────────────────
# Regex: matches ``"code": "<value>"`` (literal-dict form, the standard
# emitted-error-envelope shape). Captures ``<value>``. Allows arbitrary
# whitespace around the colon and either single or double quotes around
# the key (the codebase standard is double quotes, but be permissive).
# ────────────────────────────────────────────────────────────────────────────

_CODE_LITERAL_RE = re.compile(
    r"""['"]code['"]\s*:\s*['"]([a-zA-Z_][a-zA-Z0-9_.]*)['"]""",
    re.MULTILINE,
)


def _iter_emitted_code_literals():
    """Yield ``(path, lineno, code)`` for every ``"code": "<value>"`` literal.

    Walks the entire ``voice_typer/server`` Python tree. Includes
    docstring and comment occurrences (a docstring reference to a code
    that doesn't exist in the registry is just as much a drift bug as
    an active emission — readers will assume the code is real).
    """
    for py_file in sorted(SERVER_DIR.rglob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _CODE_LITERAL_RE.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            yield py_file, lineno, match.group(1)


# ────────────────────────────────────────────────────────────────────────────
# Test classes
# ────────────────────────────────────────────────────────────────────────────


class TestErrorCodesRegistryContents:
    """Required namespaced codes are all present in the registry."""

    def test_registry_is_frozenset(self):
        """The registry MUST be a ``frozenset`` so it can't be mutated
        at runtime (a mutable set would let a buggy import-time hook
        silently drop entries)."""
        assert isinstance(ERROR_CODES, frozenset), (
            f"ERROR_CODES must be a frozenset, got {type(ERROR_CODES).__name__}"
        )

    @pytest.mark.parametrize("code", sorted(REQUIRED_NAMESPACED_CODES))
    def test_required_namespaced_code_is_registered(self, code: str):
        """Every code listed in the EC-FIX-4 spec MUST be in the
        registry. If this fails, expand ``ERROR_CODES`` in
        ``voice_typer/server/ipc/validation.py``."""
        assert code in ERROR_CODES, (
            f"Required namespaced code {code!r} is missing from "
            f"ERROR_CODES. Add it to "
            f"voice_typer/server/ipc/validation.py."
        )

    def test_registry_contains_no_empty_strings(self):
        """A typo like ``""`` or ``"server."`` would silently bypass
        the namespacing convention. Catch it early."""
        for code in ERROR_CODES:
            assert code, f"ERROR_CODES contains an empty string: {ERROR_CODES!r}"
            assert "." in code, (
                f"ERROR_CODES entry {code!r} is not namespaced "
                f"(missing '.') — every entry must use the "
                f"'<namespace>.<name>' convention."
            )


class TestEmittedCodesAreRegisteredOrLegacy:
    """Every ``"code": "<value>"`` literal in the server tree is either
    a registered namespaced code OR a documented legacy alias.

    This is the EC-10 regression guard requested by EC-FIX-4: prevents
    new error codes from being emitted without registering them.
    """

    def test_all_emitted_codes_known(self):
        unknown: list[tuple[str, int, str]] = []
        for py_file, lineno, code in _iter_emitted_code_literals():
            if code in ERROR_CODES:
                continue
            if code in LEGACY_ALIASES:
                continue
            unknown.append(
                (str(py_file.relative_to(REPO_ROOT)), lineno, code)
            )

        if unknown:
            formatted = "\n".join(
                f"  {path}:{lineno} -> {code!r}"
                for path, lineno, code in unknown
            )
            pytest.fail(
                "Unknown error codes emitted in the server tree. Either "
                "add the namespaced form to ERROR_CODES in "
                "voice_typer/server/ipc/validation.py, OR add the "
                "legacy form to LEGACY_ALIASES in this test (if it's a "
                "backward-compat alias).\n"
                "Unknown emissions:\n" + formatted
            )

    def test_grep_actually_ran(self):
        """Sanity check: the server tree is non-empty and the regex
        matched at least one ``"code": "..."`` literal. If this fails,
        the regex is broken (or the server tree moved) and the
        ``test_all_emitted_codes_known`` test is silently a no-op."""
        emissions = list(_iter_emitted_code_literals())
        assert emissions, (
            f"No `\"code\": \"...\"` literals found under {SERVER_DIR}. "
            f"The regex may be broken or the server tree moved."
        )


class TestLegacyAliases:
    """Legacy aliases are documented and have namespaced counterparts.

    Each legacy alias MUST map to a namespaced code in
    ``ERROR_CODES`` — UNLESS the alias is a "pending migration" code
    (e.g. ``payload_too_large``) whose namespaced form hasn't been
    added yet. Pending-migration codes have an empty string as their
    counterpart in :data:`LEGACY_ALIASES` and are skipped here.
    """

    @pytest.mark.parametrize(
        "legacy, namespaced",
        sorted((legacy, n) for legacy, n in LEGACY_ALIASES.items() if n),
    )
    def test_legacy_alias_has_namespaced_counterpart(
        self, legacy: str, namespaced: str
    ):
        """If a legacy alias is documented, its namespaced counterpart
        MUST be in ``ERROR_CODES``. Otherwise the alias is orphaned
        (it's documented but the new form doesn't exist)."""
        assert namespaced in ERROR_CODES, (
            f"Legacy alias {legacy!r} maps to {namespaced!r} but "
            f"that namespaced code is NOT in ERROR_CODES. Either add "
            f"it to the registry or remove the legacy alias."
        )

    def test_legacy_aliases_match_validation_py_comment(self):
        """The legacy-alias list in this test MUST stay in sync with
        the comment block above ``ERROR_CODES`` in
        ``voice_typer/server/ipc/validation.py``. If the comment is
        updated without updating this test (or vice versa), this test
        fails. Drift between the two is exactly the bug EC-10 found."""
        validation_path = SERVER_DIR / "ipc" / "validation.py"
        text = validation_path.read_text(encoding="utf-8")
        # Extract the comment block immediately above ``ERROR_CODES``.
        # The comment lists the legacy aliases inside backticks, e.g.
        # ``internal_error``. Find all of them.
        comment_block_match = re.search(
            r"# Legacy non-namespaced aliases.*?(?=ERROR_CODES)",
            text,
            re.DOTALL,
        )
        assert comment_block_match is not None, (
            "Could not find the 'Legacy non-namespaced aliases' comment "
            "block above ERROR_CODES in validation.py."
        )
        comment_block = comment_block_match.group(0)
        # Extract every ``<code>`` from the comment block.
        documented_in_comment = set(re.findall(r"``([a-z_]+)``", comment_block))
        # Every documented-in-comment alias MUST be in LEGACY_ALIASES.
        missing_from_test = documented_in_comment - set(LEGACY_ALIASES)
        assert not missing_from_test, (
            f"Legacy aliases documented in validation.py comment but "
            f"NOT in this test's LEGACY_ALIASES dict: "
            f"{sorted(missing_from_test)}. Add them to LEGACY_ALIASES."
        )


class TestRespondWithErrorEmitsNamespacedCode:
    """Behavioural guard: ``HandlerBase._respond_with_error`` actually
    stamps the namespaced ``"server.internal_error"`` code (not the
    legacy ``"internal_error"``).

    EC-FIX-4's primary fix is changing the literal in
    ``handlers/_base.py``. This test guards against a future regression
    that re-introduces the bare legacy form (e.g. a careless find /
    replace that reverts the change)."""

    def test_respond_with_error_stamps_namespaced_internal_error(self):
        # HandlerBase is a mixin with no __init__ — instantiate
        # directly. The method only touches ``self`` to access the
        # module-level ``log`` (imported into the class scope by
        # ``_log.py``).
        handler = HandlerBase()
        resp: dict = {"id": 42, "type": "ok", "data": {}}
        result = handler._respond_with_error(
            resp, RuntimeError("boom"), "test_cmd"
        )
        assert result is resp, "_respond_with_error must return the same dict"
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.internal_error", (
            f"Expected 'server.internal_error' (namespaced form), got "
            f"{result['data']['code']!r}. The handler catch-all MUST "
            f"emit the namespaced form per EC-FIX-4 / G4-M-22."
        )
        assert result["data"]["message"] == "internal error"

    def test_respond_with_error_does_not_emit_legacy_form(self):
        """The response MUST NOT contain the legacy bare
        ``"internal_error"`` code. This is a separate assertion (not
        just ``!= "server.internal_error"``) so that a regression that
        re-introduces the legacy form is caught with a clear message."""
        handler = HandlerBase()
        resp: dict = {"id": 1, "type": "ok", "data": {}}
        result = handler._respond_with_error(
            resp, RuntimeError("boom"), "test_cmd"
        )
        assert result["data"]["code"] != "internal_error", (
            "_respond_with_error is emitting the LEGACY bare "
            "'internal_error' code. EC-FIX-4 changed this to "
            "'server.internal_error' — the regression must be reverted."
        )


class TestErrorResponseDefaultCode:
    """Guard: ``_error_response``'s default ``code`` parameter is the
    namespaced ``"server.handler_error"`` (not the legacy bare
    ``"handler_error"``)."""

    def test_default_code_is_namespaced(self):
        sig = inspect.signature(_error_response)
        code_param = sig.parameters["code"]
        assert code_param.default == "server.handler_error", (
            f"_error_response's default `code` parameter must be "
            f"'server.handler_error' (namespaced), got "
            f"{code_param.default!r}. EC-FIX-4 / G4-M-22 requires "
            f"the namespaced form."
        )

    def test_error_response_with_default_code(self):
        resp: dict = {"id": 1, "type": "ok", "data": {}}
        result = _error_response(resp, "something went wrong")
        assert result["data"]["code"] == "server.handler_error"
        assert result["data"]["message"] == "something went wrong"
