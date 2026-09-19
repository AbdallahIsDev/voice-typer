"""Contract test for the IPC error-code registry."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import ERROR_CODES, _error_response

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SERVER_DIR = REPO_ROOT / "voice_typer" / "server"
CLIENT_DIR = REPO_ROOT / "voice_typer" / "client"

TS_ERROR_CODES_CANDIDATE_PATHS = (
    CLIENT_DIR / "src" / "renderer" / "src" / "types" / "ipc" / "enums.ts",
    CLIENT_DIR / "src" / "renderer" / "src" / "types" / "ipc.ts",
)


# Every code in this set MUST be in ``ERROR_CODES``. If a code is
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
        # Pre-existing namespaced codes (kept for stability, these
        "client.invalid_field",
        "client.missing_field",
        "client.path_not_allowed",
        "client.not_found",
        "server.file_locked",
        "server.model_switch_failed",
    }
)


# Each entry maps a legacy non-namespaced code → its namespaced
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
    "payload_too_large": "",
    "not_initialized": "",
    # Rust-host-only dispatch-cap codes emitted by the Tauri
    "pending_full": "",
    "data_too_large": "",
    "disallowed_command": "",
    "disallowed_window": "",
    "sidecar_disconnected": "",
    "not_found": "client.not_found",
}


_CODE_LITERAL_RE = re.compile(
    r"""['"]code['"]\s*:\s*['"]([a-zA-Z_][a-zA-Z0-9_.]*)['"]""",
    re.MULTILINE,
)


def _iter_emitted_code_literals():
    """Yield ``(path, lineno, code)`` for every ``\"code\": \"<value>\"`` literal."""
    for py_file in sorted(SERVER_DIR.rglob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _CODE_LITERAL_RE.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            yield py_file, lineno, match.group(1)


class TestErrorCodesRegistryContents:
    """Required namespaced codes are all present in the registry."""

    def test_registry_is_frozenset(self):
        """The registry MUST be a ``frozenset`` so it can't be mutated"""
        assert isinstance(ERROR_CODES, frozenset), f"ERROR_CODES must be a frozenset, got {type(ERROR_CODES).__name__}"

    @pytest.mark.parametrize("code", sorted(REQUIRED_NAMESPACED_CODES))
    def test_required_namespaced_code_is_registered(self, code: str):
        """``voice_typer/server/ipc/validation.py``."""
        assert code in ERROR_CODES, (
            f"Required namespaced code {code!r} is missing from "
            f"ERROR_CODES. Add it to "
            f"voice_typer/server/ipc/validation.py."
        )

    def test_registry_contains_no_empty_strings(self):
        """A typo like ``\"\"`` or ``\"server.\"`` would silently bypass"""
        for code in ERROR_CODES:
            assert code, f"ERROR_CODES contains an empty string: {ERROR_CODES!r}"
            assert "." in code, (
                f"ERROR_CODES entry {code!r} is not namespaced "
                f"(missing '.'), every entry must use the "
                f"'<namespace>.<name>' convention."
            )


class TestEmittedCodesAreRegisteredOrLegacy:
    """Every ``\"code\": \"<value>\"`` literal in the server tree is either"""

    def test_all_emitted_codes_known(self):
        unknown: list[tuple[str, int, str]] = []
        for py_file, lineno, code in _iter_emitted_code_literals():
            if code in ERROR_CODES:
                continue
            if code in LEGACY_ALIASES:
                continue
            unknown.append((str(py_file.relative_to(REPO_ROOT)), lineno, code))

        if unknown:
            formatted = "\n".join(f"  {path}:{lineno} -> {code!r}" for path, lineno, code in unknown)
            pytest.fail(
                "Unknown error codes emitted in the server tree. Either "
                "add the namespaced form to ERROR_CODES in "
                "voice_typer/server/ipc/validation.py, OR add the "
                "legacy form to LEGACY_ALIASES in this test (if it's a "
                "backward-compat alias).\n"
                "Unknown emissions:\n" + formatted
            )

    def test_grep_actually_ran(self):
        """Sanity check: the server tree is non-empty and the regex"""
        emissions = list(_iter_emitted_code_literals())
        assert emissions, (
            f'No `"code": "..."` literals found under {SERVER_DIR}. The regex may be broken or the server tree moved.'
        )


class TestLegacyAliases:
    """Legacy aliases are documented and have namespaced counterparts."""

    @pytest.mark.parametrize(
        "legacy, namespaced",
        sorted((legacy, n) for legacy, n in LEGACY_ALIASES.items() if n),
    )
    def test_legacy_alias_has_namespaced_counterpart(self, legacy: str, namespaced: str):
        """If a legacy alias is documented, its namespaced counterpart"""
        assert namespaced in ERROR_CODES, (
            f"Legacy alias {legacy!r} maps to {namespaced!r} but "
            f"that namespaced code is NOT in ERROR_CODES. Either add "
            f"it to the registry or remove the legacy alias."
        )

    def test_legacy_aliases_match_validation_py_comment(self):
        """``voice_typer/server/ipc/validation.py``. The registry moved"""
        from voice_typer.server.ipc.validation import LEGACY_ERROR_CODES

        documented = set(LEGACY_ERROR_CODES)
        # Every canonical legacy code MUST be in LEGACY_ALIASES.
        missing_from_test = documented - set(LEGACY_ALIASES)
        assert not missing_from_test, (
            f"Legacy aliases in validation.LegacyErrorCodes but "
            f"NOT in this test's LEGACY_ALIASES dict: "
            f"{sorted(missing_from_test)}. Add them to LEGACY_ALIASES."
        )


class TestRespondWithErrorEmitsNamespacedCode:
    """Behavioural guard: ``HandlerBase._respond_with_error`` actually"""

    def test_respond_with_error_stamps_namespaced_internal_error(self):
        # HandlerBase is a mixin with no __init__, instantiate
        handler = HandlerBase()
        resp: dict = {"id": 42, "type": "ok", "data": {}}
        result = handler._respond_with_error(resp, RuntimeError("boom"), "test_cmd")
        assert result is resp, "_respond_with_error must return the same dict"
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.internal_error", (
            f"Expected 'server.internal_error' (namespaced form), got "
            f"{result['data']['code']!r}. The handler catch-all MUST "
            f"emit the namespaced."
        )
        assert result["data"]["message"] == "internal error"

    def test_respond_with_error_does_not_emit_legacy_form(self):
        """The response MUST NOT contain the legacy bare"""
        handler = HandlerBase()
        resp: dict = {"id": 1, "type": "ok", "data": {}}
        result = handler._respond_with_error(resp, RuntimeError("boom"), "test_cmd")
        assert result["data"]["code"] != "internal_error", (
            "_respond_with_error is emitting the LEGACY bare "
            "'internal_error' code."
            "'server.internal_error', the regression must be reverted."
        )


class TestErrorResponseDefaultCode:
    """namespaced ``\"server.handler_error\"`` (not the legacy bare"""

    def test_default_code_is_namespaced(self):
        sig = inspect.signature(_error_response)
        code_param = sig.parameters["code"]
        assert code_param.default == "server.handler_error", (
            f"_error_response's default `code` parameter must be "
            f"'server.handler_error' (namespaced). "
            f"{code_param.default!r}."
            f"the namespaced form."
        )

    def test_error_response_with_default_code(self):
        resp: dict = {"id": 1, "type": "ok", "data": {}}
        result = _error_response(resp, "something went wrong")
        assert result["data"]["code"] == "server.handler_error"
        assert result["data"]["message"] == "something went wrong"


# Matches a single line of the form ``| "some.code"`` (with optional
_TS_UNION_MEMBER_RE = re.compile(
    r"""^\s*\|\s*["']([a-zA-Z0-9_.]+)["']\s*,?\s*(?://.*)?$""",
    re.MULTILINE,
)


def _find_ts_error_codes_file() -> Path:
    """Return the path to the TS file that declares the ``ErrorCodes`` union."""
    for candidate in TS_ERROR_CODES_CANDIDATE_PATHS:
        if candidate.is_file():
            return candidate
    pytest.fail(
        "Could not find the TS ErrorCodes declaration file. Tried: "
        + ", ".join(str(p) for p in TS_ERROR_CODES_CANDIDATE_PATHS)
    )


def _parse_ts_error_codes_union(text: str) -> set[str]:
    """Extract the set of string-literal members from a TS string-literal union."""
    anchor = re.search(r"\btype\s+ErrorCodes\b", text)
    if anchor is None:
        pytest.fail(
            "Could not find `type ErrorCodes` declaration in the TS file, the union may have been renamed or moved."
        )
    tail = text[anchor.start() :]
    terminator = tail.find(";")
    # No terminator found, parse to end of file (defensive).
    block = tail if terminator == -1 else tail[:terminator]
    return {m.group(1) for m in _TS_UNION_MEMBER_RE.finditer(block)}


class TestTsErrorCodesParity:
    """renderer's TS ``ErrorCodes`` union."""

    def test_ts_error_codes_file_exists(self):
        """Sanity check: the TS file declaring ``ErrorCodes`` exists."""
        path = _find_ts_error_codes_file()
        assert path.is_file(), f"TS ErrorCodes file not found at {path}"

    def test_python_error_codes_are_subset_of_ts_union(self):
        """Every Python namespaced code MUST be present in the TS union."""
        path = _find_ts_error_codes_file()
        text = path.read_text(encoding="utf-8")
        ts_codes = _parse_ts_error_codes_union(text)
        assert ts_codes, (
            "Parsed zero entries from the TS ErrorCodes union, the regex may be broken or the union was reformatted."
        )
        missing = ERROR_CODES - ts_codes
        assert not missing, (
            "Python ERROR_CODES entries missing from the TS ErrorCodes "
            f"union in {path.relative_to(REPO_ROOT)}: {sorted(missing)}. "
            'Add each missing code as a `| "<code>"` member of the '
            "ErrorCodes union so the renderer's error-envelope switch "
            "can branch on it."
        )

    def test_ts_union_includes_rust_host_only_codes(self):
        """the TS union MUST include the two Rust-host-only codes"""
        path = _find_ts_error_codes_file()
        text = path.read_text(encoding="utf-8")
        ts_codes = _parse_ts_error_codes_union(text)
        for rust_code in ("disallowed_window", "disallowed_command"):
            assert rust_code in ts_codes, (
                f"Rust-host-only code {rust_code!r} is missing from the "
                f"TS ErrorCodes union in {path.relative_to(REPO_ROOT)}. "
                "The Rust `#[tauri::command]` functions emit this code "
                "BEFORE dispatch reaches the Python sidecar (see "
                "`src-tauri/src/commands/mod.rs::require_main_window` "
                "and `src-tauri/src/commands/sidecar_cmds.rs`); the "
                "renderer's error-envelope switch MUST have a case for it."
            )

    def test_ts_union_includes_rust_dispatch_cap_codes(self):
        """the TS union MUST include the two bare Rust dispatch-cap"""
        path = _find_ts_error_codes_file()
        text = path.read_text(encoding="utf-8")
        ts_codes = _parse_ts_error_codes_union(text)
        for rust_code in ("pending_full", "data_too_large"):
            assert rust_code in ts_codes, (
                f"Rust dispatch-cap code {rust_code!r} is missing from the "
                f"TS ErrorCodes union in {path.relative_to(REPO_ROOT)}. "
                "The Tauri dispatch layer emits this BARE code before "
                "dispatch reaches the Python sidecar; the renderer's "
                "error-envelope switch MUST have a case for it."
            )
