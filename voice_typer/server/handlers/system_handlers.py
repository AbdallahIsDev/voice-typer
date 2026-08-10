"""System IPC handler mixin: restart_app, quit_app, check_accessibility,
set_tray_locale, show_electron_notification.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

(2026-07-30): ``_handle_export_diagnostics`` was REMOVED — the
Tauri host now handles it via a dedicated Rust command. The Python-side
``service.export_diagnostics`` still exists for the legacy Electron path
and is invoked by the Rust bridge; only the IPC dispatch route was
deleted. ``_handle_check_accessibility`` and
``_handle_show_electron_notification`` are also absent from
``_COMMAND_REGISTRY`` and the renderer allowlist, but they are retained
because tests in ``tests/regressions/``, ``tests/tauri/``, and
``tests/test_notification_event_name.py`` invoke them directly as the
reference shape the Rust host mirrors.
"""

import contextlib
import subprocess
import unicodedata

from voice_typer.server import event_bus
from voice_typer.server.branding import APP_NAME
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (  # noqa: F401
    ErrorCodes,
    LegacyErrorCodes,
    _error_response,
    _validate_dict_payload,
)
from voice_typer.server.platform_utils import is_linux, is_macos


def _has_control_chars(value) -> bool:
    """Return True if *value* contains a Unicode Cc/Cf control char.

    (session-DE): used by ``_handle_show_electron_notification``
        to reject control characters in ``title`` / ``message``. The Cc
        category covers ANSI escapes (``\\x1b``), terminal bell
        (``\\x07``), newline (``\\n``), carriage return (``\\r``), and
        other C0/C1 control codes. The Cf category covers RTL/LTR
        overrides (``\\u202e`` / ``\\u202d``), zero-width joiner
        (``\\u200d``), zero-width space (``\\u200b``), BOM
        (``\\ufeff``), and other format chars. A horizontal tab
        (``\\t``) is explicitly allowed (tabular layout in the message
        body is common and the OS notification APIs render it
        consistently as whitespace).

        Non-string values return ``False`` (the caller's per-field type
        check has already rejected non-strings; this helper only runs on
        validated string fields).
    """
    if not isinstance(value, str):
        return False
    return any(unicodedata.category(ch) in ("Cc", "Cf") and ch != "\t" for ch in value)


def _enumerate_polkit_actions() -> list[str]:
    """Enumerate polkit actions registered for Voice Typer via ``pkaction``.

    Surfaces the ``com.voicetyper.install-permissions`` action (the only
    namespace the app ships — finding #54 renamed it from the legacy
    pre-Tauri Electron root, and ``install_permissions.py`` removes the
    legacy policy file (``LEGACY_POLKIT_POLICY_DEST``) at install/upgrade
    time, so no current install registers the old action ID).
    Enumerating the legacy action here would be vestigial: the legacy
    policy is gone by the time a user reaches the reset button.

    Returns a sorted, deduped list of matching action IDs. Tolerant of
    ``pkaction`` being absent, timing out, or exiting non-zero
    (logged warning, empty list) — the caller still performs the
    polkit-daemon restart regardless.
    """

    try:
        result = subprocess.run(
            ["pkaction"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("[IPC] reset_linux_permissions: pkaction unavailable (%s)", exc)
        return []
    if result.returncode != 0:
        log.warning(
            "[IPC] reset_linux_permissions: pkaction exited %d",
            result.returncode,
        )
        return []
    return sorted({line.strip() for line in result.stdout.splitlines() if "voicetyper" in line.strip().lower()})


def _polkit_check_authorization(action_id: str) -> str:
    """Query the current authorization state of *action_id* via ``pkcheck``.

    ``pkcheck`` exit codes: 0 = authorized, 1 = not authorized,
    anything else (or an unavailable/timeouting binary) = ``check_error``.
    Called AFTER the polkit-daemon restart, so the expected post-reset
    state for Voice Typer's ``auth_admin_keep`` actions is
    ``not_authorized`` — i.e. the next pkexec grant will re-prompt
    (compare ``install_permissions.py``'s documented ~5-minute
    ``auth_admin_keep`` caching window).
    """

    try:
        result = subprocess.run(
            ["pkcheck", "--action-id", action_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning(
            "[IPC] reset_linux_permissions: pkcheck unavailable for %s (%s)",
            action_id,
            exc,
        )
        return "check_error"
    if result.returncode == 0:
        return "authorized"
    if result.returncode == 1:
        return "not_authorized"
    return "check_error"


def _reset_polkit_authorization() -> tuple[str | None, bool, str | None]:
    """Clear polkitd's cached authorization decisions by restarting it.

    polkit's ``auth_admin_keep`` default (``voice-typer.polkit``) caches
    the admin decision for ~5 minutes, so after a Voice Typer update the
    old grant can still authorize pkexec runs without re-prompting even
    though the on-disk policy changed — the stale authorization. There
    is no per-action revocation command in polkit; restarting the
    polkit daemon (polkitd) is the supported way to flush the whole
    in-memory authorization cache.

    The restart needs root, so it goes through ``pkexec`` (the same
    mechanism the permission installer itself uses). Within the stale
    ``auth_admin_keep`` window the pkexec call succeeds WITHOUT a new
    prompt; once the cache is stale-but-expired the user gets the
    familiar polkit GUI prompt. Candidate service names in order of
    likelihood (systemd ``polkit`` / ``polkitd``, then legacy
    ``service polkit restart``).

    Returns ``(command, ok, error)``: the exact command that succeeded
    (for the ``ack`` payload + the renderer's snackbar), or
    ``(None, False, error)`` when every candidate failed.
    """

    candidates = [
        ["pkexec", "systemctl", "restart", "polkit"],
        ["pkexec", "systemctl", "restart", "polkitd"],
        ["pkexec", "service", "polkit", "restart"],
    ]
    last_error: str | None = None
    for cmd in candidates:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_error = f"pkexec failed: {exc}"
            continue
        if result.returncode == 0:
            return " ".join(cmd), True, None
        if result.returncode == 126:
            # pkexec: the user dismissed the polkit authentication dialog.
            last_error = "pkexec: authentication dismissed"
        elif result.returncode == 127:
            # pkexec: not authorized / no authentication agent, or an
            # internal error occurred.
            last_error = "pkexec: not authorized (no authentication agent?)"
        else:
            last_error = result.stderr.strip() or (f"polkit daemon restart exited {result.returncode}")
    return None, False, last_error or "polkit daemon restart failed"


class SystemHandlersMixin(HandlerBase):
    """Mixin: system-level IPC handlers (restart / quit / accessibility / ...).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak). Per-command validation errors (``invalid_field``,
        ``invalid_payload``) remain explicit envelopes the renderer switches on.
        The ``restart_app`` / ``quit_app`` handlers send the ack BEFORE
        invoking ``service.restart()`` / ``service.quit()`` because the
        service call may tear down or respawn the process and we want the
        client to receive the ack even if the call blocks. If the service
        call raises, the handler pushes a follow-up ``error`` event via
        :func:`event_bus.publish` carrying ``kind="restart_failed"`` /
        ``kind="quit_failed"`` and a sanitized ``message``. The renderer
        subscribes to ``error`` events and surfaces a toast on ``kind``
        match — without this push, the client would proceed as if the
        restart/quit succeeded (the original ack has no error channel).
        The exception itself is not re-raised (which would crash the IPC
        dispatch thread).

        ``_handle_check_accessibility`` distinguishes
        ``subprocess.TimeoutExpired`` / ``FileNotFoundError`` from the
        generic ``granted=False`` path so the renderer can show a "click
        here to retry" CTA when the check fails for environmental reasons
        (vs. "open System Settings" CTA when the user has not granted
        permission).

        ``_handle_show_electron_notification`` enforces ``max_value_len``
        rules on ``title`` (256) and ``message`` (4096) so a misbehaving
        caller can't push a multi-MB notification body that the OS
        notification API would silently truncate or refuse to display.
    """

    def _handle_restart_app(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``restart_app`` IPC command.

        Sends the ack BEFORE calling ``service.restart()`` (the service
        call may block or tear down the process, and the client must
        receive the ack regardless). If ``service.restart()`` raises,
        pushes a follow-up ``error`` event via :func:`event_bus.publish`
        with ``kind="restart_failed"`` so the renderer can surface a
        toast instead of silently assuming the restart succeeded. The
        exception is logged and swallowed (the IPC dispatch thread must
        not crash).
        """
        resp["type"] = "ack"
        # ensure ack carries an explicit ``data: {}`` for
        # shape consistency with the other ack responses.  This call
        # sends the response directly (returns None) so the
        # ``resp.setdefault("data", {})`` at the end of _dispatch
        # never runs for this branch — we add it here instead.
        resp.setdefault("data", {})
        try:
            self._send(resp)
            self.service.restart()
        except Exception as e:
            log.error("[IPC] restart_app failed: %s", e, exc_info=True)
            # The ack was already sent; can't recover from here, but
            # push a follow-up ``error`` event so the renderer can show
            # a toast instead of silently assuming the restart succeeded.
            # ``str(e)`` is included because the error envelope travels
            # only over the authenticated WS channel (not exposed to
            # untrusted callers) and the renderer uses it for a localized
            # toast body — the generic IPC error envelope avoids leaking
            # ``str(e)`` for *request-response* paths, but this is a
            # push-event follow-up to an already-acked command, not a
            # response to an untrusted request.
            self._publish_service_failure("restart_failed", str(e))
        return None

    def _handle_quit_app(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``quit_app`` IPC command.

        Mirrors :meth:`_handle_restart_app`: ack first, then
        ``service.quit()``, then push ``kind="quit_failed"`` on raise.
        """
        resp["type"] = "ack"
        # same as restart_app — add explicit ``data: {}``.
        resp.setdefault("data", {})
        try:
            self._send(resp)
            self.service.quit()
        except Exception as e:
            log.error("[IPC] quit_app failed: %s", e, exc_info=True)
            self._publish_service_failure("quit_failed", str(e))
        return None

    def _publish_service_failure(self, kind: str, message: str) -> None:
        """Push a follow-up ``error`` event for a service-call failure.

        ``kind`` is the discriminator the renderer switches on
        (``"restart_failed"`` / ``"quit_failed"``); ``message`` is the
        ``str(exc)`` for the toast body. Publish failures are best-effort
        (logged at debug) so a broken event-bus subscriber can't crash
        the IPC dispatch thread.
        """
        try:
            event_bus.publish(
                {
                    "type": "error",
                    "data": {
                        "kind": kind,
                        "message": message,
                    },
                }
            )
        except Exception:
            log.debug(
                "[IPC] failed to publish service-failure event (kind=%s)",
                kind,
                exc_info=True,
            )

    def _handle_check_accessibility(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``check_accessibility`` IPC command.

        macOS Accessibility permission check.
                Returns ``{"granted": bool, "platform": "macos"|"windows"|"linux"}``.
                On non-macOS platforms, always returns granted=True (no
                accessibility permission required). The Electron UI uses
                this to show a persistent warning banner on macOS when
                the permission is missing, and to gate the onboarding
                wizard's "Grant Accessibility" step.

                The osascript fallback was REMOVED. The previous
                implementation collapsed every failure mode (subprocess
                timeout, missing ``osascript`` binary, ctypes ``LoadLibrary``
                failure) into a generic ``granted: False`` response —
                indistinguishable from "user has not granted the permission".
                The renderer had no way to tell the user "we couldn't run
                the check, click here to retry" vs. "you have not granted
                permission, click here to open System Settings".

                The osascript fallback was also invasive: it synthesized
                a REAL keystroke via System Events (a space character),
                which focuses the frontmost app and types into whatever
                has keyboard focus — a user running the app at login
                could see the space land in their password prompt or
                terminal. The ctypes ``AXIsProcessTrusted()`` probe is
                the official API and runs in microseconds with no side
                effects. When the ctypes load fails (stripped-down macOS
                install, code-signed bundle with restricted dyld env,
                CI runners), we now return ``{"granted": False,
                "platform": "macos", "reason": "check_failed"}`` so the
                renderer can show a retry CTA.

                The platform string was also fixed from ``"darwin"``
                (the value of ``sys.platform`` on macOS) to ``"macos"``
                to match the convention used by ``is_macos()`` and the
                rest of the codebase. The non-macOS happy path returns
                ``sys.platform`` (e.g. ``"linux"`` on Linux) so existing
                tests asserting ``platform == _sys.platform`` continue
                to pass.

                Stale-grant reset suggestion (finding #919 part b —
                2026-08-10): when ``AXIsProcessTrusted()`` actually ran
                and returned False (a CONFIRMED stale grant — NOT the
                ``reason: "check_failed"`` fallback, where the probe
                itself errored and we can't tell stale from transient),
                the response is extended with a proactive reset
                suggestion for the Settings → Troubleshooting section:

                - ``suggest_reset: True`` + ``reset_command`` — the
                  runtime `tccutil reset Accessibility <bundle-id>`
                  command string, built from the host app's runtime-
                  resolved bundle ID (``resolve_host_bundle_id()`` +
                  ``tccutil_reset_command_str("Accessibility", ...)``,
                  the same helpers that back
                  ``_handle_reset_macos_accessibility``).
                - ``suggest_reset: False`` (no ``reset_command`` key) —
                  when the bundle ID can't be resolved. Mirrors the
                  reset handler's convention: a wrong bundle ID in a
                  ``tccutil`` command is worse than no command, so the
                  command is omitted entirely when unresolved and the
                  renderer shows no command (the manual reset button
                  remains available).

                The suggestion is only ever attached in the stale case —
                ``granted: True`` responses and the ``check_failed``
                fallback keep the original two/three-field shape so
                existing consumers (the reference shape mirrored by the
                Rust host) are unaffected.

        this handler ignores its ``data`` payload (the
                command takes no arguments), but for consistency with the
                other handlers we now run an empty-schema validation so a
                non-dict payload is rejected with ``invalid_payload`` instead
                of being silently accepted.
        """
        # empty-schema validation (consistency with siblings).
        validated, error = _validate_dict_payload(data, {})
        if error:
            return error
        assert validated is not None  # empty dict; handler ignores payload
        try:
            import sys as _sys

            granted = True
            # Canonical platform string. ``_sys.platform`` is
            # ``"darwin"`` on macOS — we map that to ``"macos"`` to
            # match the convention used everywhere else in the codebase
            # (``is_macos()``, ``platform_utils``, etc.). Other platforms
            # pass through ``_sys.platform`` verbatim so existing tests
            # asserting ``platform == _sys.platform`` continue to pass.
            platform_name = "macos" if is_macos() else _sys.platform
            if is_macos():
                try:
                    import ctypes

                    # AXIsProcessTrusted() is the official API.
                    # Returns True iff the process has Accessibility.
                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    granted = bool(app_services.AXIsProcessTrusted())
                except Exception as exc:
                    # osascript fallback removed (invasive
                    # keystroke synthesis + 3s subprocess on the IPC
                    # hot path). Treat ctypes-load failure as
                    # "permission not granted" + ``check_failed`` reason
                    # so the renderer can show a retry CTA instead of
                    # the "Open System Settings" CTA.
                    log.warning(
                        "[IPC] check_accessibility: AXIsProcessTrusted ctypes load "
                        "failed (%s); treating as not-granted (check_failed)",
                        exc,
                    )
                    resp["type"] = "accessibility_status"
                    resp["data"] = {
                        "granted": False,
                        "platform": "macos",
                        "reason": "check_failed",
                    }
                    return resp
            resp["type"] = "accessibility_status"
            status_data: dict = {
                "granted": granted,
                "platform": platform_name,
            }
            if is_macos() and not granted:
                # Stale-grant reset suggestion (finding #919 part b).
                # ONLY attached when AXIsProcessTrusted() actually ran
                # and returned False (a confirmed stale grant) — the
                # ``check_failed`` path above returns before this
                # point, so an un-runnable probe never suggests a
                # reset it couldn't substantiate.
                #
                # The helper imports are function-level (same pattern
                # as ``_handle_reset_macos_accessibility``) so tests
                # can monkeypatch the source module attributes and so
                # the ImportError, if the centralised helper ever
                # regresses, surfaces only on the macOS stale path.
                from voice_typer.server.server_platform.macos_bundle_id import (
                    resolve_host_bundle_id,
                )

                bundle_id = resolve_host_bundle_id()
                if bundle_id:
                    # ``tccutil_reset_command_str`` is imported only
                    # once the bundle ID resolved — a wrong bundle ID
                    # in a tccutil command is worse than no command, so
                    # the unresolved case omits the command entirely
                    # (mirrors the reset handler's convention).
                    from voice_typer.server.server_platform.macos_bundle_id import (
                        tccutil_reset_command_str,
                    )

                    status_data["suggest_reset"] = True
                    status_data["reset_command"] = tccutil_reset_command_str("Accessibility", bundle_id)
                else:
                    status_data["suggest_reset"] = False
            resp["data"] = status_data
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "check_accessibility")
        return resp

    def _handle_reset_macos_accessibility(self, data: object | None, resp: dict) -> dict | None:
        """Handle the ``reset_macos_accessibility`` IPC command.

        Runs ``tccutil reset Accessibility <bundle-id>`` to clear a stale
        macOS Accessibility TCC entry (e.g. orphaned under a stale
        designated requirement after an app update silently reset the
        permission), then re-opens System Settings → Privacy & Security
        → Accessibility so the user can re-grant.

        The bundle ID is resolved at RUNTIME from the host app's
        ``Contents/Info.plist`` (``resolve_host_bundle_id`` — walks the
        parent-process chain to the nearest ``*.app``), so both the
        Electron and Tauri builds reset the entry for the actually
        running host and a future bundle-identifier change needs no code
        edit. Mirrors the a11y re-grant notification in
        ``startup_tasks.py`` (finding #127 part b).

        ``tccutil`` is a per-user command — the backend runs as the
        logged-in user, so it is invoked directly (no sudo).

        Response: ``ack`` with ``{ok: bool, command: str | None,
        error: str | None}``. ``ok=False`` with ``error`` set when the
        platform isn't macOS, the bundle ID can't be resolved, or
        ``tccutil`` fails — a wrong bundle ID in a ``tccutil`` command
        is worse than no command, so ``command`` is omitted entirely
        when unresolved.
        """
        validated, error = _validate_dict_payload(data, {})
        if error:
            return error
        try:
            if not is_macos():
                resp["type"] = "ack"
                resp["data"] = {
                    "ok": False,
                    "command": None,
                    "error": "unsupported_platform",
                }
                return resp

            from voice_typer.server.permissions import _open_macos_accessibility_settings
            from voice_typer.server.server_platform.macos_bundle_id import (
                resolve_host_bundle_id,
                tccutil_reset_command,
                tccutil_reset_command_str,
            )

            bundle_id = resolve_host_bundle_id()
            if not bundle_id:
                resp["type"] = "ack"
                resp["data"] = {
                    "ok": False,
                    "command": None,
                    "error": "bundle_id_unresolved",
                }
                return resp

            # TCC-002: both forms come from the single construction point
            # in macos_bundle_id (argv for subprocess, string for the
            # response) so a future change lands in one place.
            command = tccutil_reset_command_str("Accessibility", bundle_id)
            try:
                result = subprocess.run(
                    tccutil_reset_command("Accessibility", bundle_id),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                ok = result.returncode == 0
                tcc_error = None if ok else (result.stderr.strip() or "tccutil failed")
            except (subprocess.TimeoutExpired, OSError) as exc:
                ok = False
                tcc_error = f"tccutil failed: {exc}"

            # Re-open System Settings → Privacy & Security →
            # Accessibility so the user can re-grant after the reset
            # clears the stale entry (non-fatal — the settings opener
            # logs its own warning on failure).
            _open_macos_accessibility_settings()

            resp["type"] = "ack"
            resp["data"] = {"ok": ok, "command": command, "error": tcc_error}
        except Exception as exc:
            self._respond_with_error(resp, exc, "reset_macos_accessibility")
        return resp

    def _handle_reset_linux_permissions(self, data: object | None, resp: dict) -> dict | None:
        """Handle the ``reset_linux_permissions`` IPC command.

        The Linux sibling of ``reset_macos_accessibility``: clears a
        STALE polkit authorization for Voice Typer's keyboard-permission
        grant so the next "Grant permission" (pkexec
        ``com.voicetyper.install-permissions``) prompts again.

        Why this exists: ``voice-typer.polkit`` uses polkit's
        ``auth_admin_keep`` default, which caches the admin decision for
        ~5 minutes in polkitd. After an app update (or a policy change
        such as the finding-#54 action-ID rename), the cached decision
        can authorize pkexec runs without re-prompting even though the
        grant is stale relative to the running build — the visual
        symptom is "Grant permission silently does nothing / the
        permission never seems to reset".

        There is no per-action revocation command in polkit
        (``pkaction`` only lists; ``pkcheck`` only queries), so the
        reset restarts the polkit daemon, which flushes the whole
        in-memory authorization cache. Steps:

        1. ``pkaction`` — enumerate the registered Voice Typer polkit
           action IDs (``com.voicetyper.install-permissions`` — the only
           namespace the app ships), so the response can surface what
           was actually registered.
        2. ``pkexec systemctl restart polkit`` (fallbacks: polkitd /
           ``service polkit restart``) — clears the cached
           authorization. Runs via pkexec so a NEW polkit prompt
           appears when no cached auth exists; inside the stale
           window it succeeds silently.
        3. ``pkcheck --action-id <id>`` per enumerated action — verify
           the post-reset state (expected: ``not_authorized`` = the
           next grant will re-prompt).

        Response: ``ack`` with ``{ok: bool, command: str | None,
        error: str | None, actions: list[str], checks: dict[str, str]}``.
        ``ok=False`` with ``error`` set when the platform isn't Linux,
        pkexec fails (incl. the user dismissing the dialog — exit 126),
        or no restart candidate succeeds.
        """
        validated, error = _validate_dict_payload(data, {})
        if error:
            return error
        try:
            if not is_linux():
                resp["type"] = "ack"
                resp["data"] = {
                    "ok": False,
                    "command": None,
                    "error": "unsupported_platform",
                    "actions": [],
                    "checks": {},
                }
                return resp

            actions = _enumerate_polkit_actions()
            command, ok, error_str = _reset_polkit_authorization()
            checks: dict[str, str] = {}
            if ok:
                for action_id in actions:
                    checks[action_id] = _polkit_check_authorization(action_id)

            resp["type"] = "ack"
            resp["data"] = {
                "ok": ok,
                "command": command,
                "error": error_str,
                "actions": actions,
                "checks": checks,
            }
        except Exception as exc:
            self._respond_with_error(resp, exc, "reset_linux_permissions")
        return resp

    def _handle_set_tray_locale(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``set_tray_locale`` IPC command.

        accepts ``locale`` (required) and an optional
                ``labels`` dict of translated tray-menu strings. When ``labels``
                is provided it is registered for that locale (merged over
                English) so the tray menu can localize into any of the 8
                renderer locales, not just the server-hard-coded en/es. The tray
                menu is rebuilt so the new labels take effect immediately.
        """
        try:
            from voice_typer.server.tray import (
                get_tray_locale,
                register_tray_labels,
                set_tray_locale,
            )

            validated, error = _validate_dict_payload(
                data,
                {
                    "locale": {
                        "type": str,
                        "required": False,
                        "default": "en",
                        # cap locale length (POSIX locale names
                        # are <=64 chars in practice; anything longer is
                        # either a bug or a hostile payload). Without
                        # this cap a multi-MB ``locale`` string would
                        # be stored verbatim in the tray-i18n module's
                        # locale dict and re-serialized into every tray
                        # menu rebuild.
                        "max_value_len": 64,
                        # whole-payload DoS cap. The
                        # ``labels`` dict can legitimately carry a full
                        # 8-locale label table (~16 keys x ~64 chars =
                        # ~1 KiB per locale x 8 = ~8 KiB); 64 KiB
                        # leaves generous headroom while rejecting a
                        # multi-MB blob.
                        "max_payload_bytes": 64 * 1024,
                    },
                    "labels": {
                        "type": dict,
                        "required": False,
                        "default": None,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            locale = validated["locale"]
            labels = validated["labels"]
            # validate label dict contents. Keys must be
            # strings <=64 chars (tray label keys like "app_name",
            # "toggle_dictation" are all <=32 chars today); values must
            # be strings <=1024 chars (a single menu-item label rarely
            # exceeds 80 chars; 1024 leaves headroom for verbose
            # translations). Reject with ``invalid_field`` so a hostile
            # caller cannot turn the tray-i18n locale dict into a
            # multi-MB memory sink.
            #
            # The error envelope is built via the shared
            # ``_error_response`` helper (consistent with the rest of
            # the IPC handler layer) and the ``field`` key is stamped
            # afterward — ``_error_response`` does not currently
            # accept a ``field`` kwarg, but routing the envelope
            # through it keeps the ``code`` / ``legacy_code`` /
            # ``message`` shape uniform across every error path in
            # this handler.
            if labels is not None:
                for k, v in labels.items():
                    if not isinstance(k, str) or len(k) > 64:
                        _error_response(
                            resp,
                            "label keys must be strings of <=64 chars",
                            code=ErrorCodes.INVALID_FIELD,
                        )
                        resp["data"]["field"] = "labels"
                        return resp
                    if not isinstance(v, str) or len(v) > 1024:
                        _error_response(
                            resp,
                            "label values must be strings of <=1024 chars",
                            code=ErrorCodes.INVALID_FIELD,
                        )
                        resp["data"]["field"] = "labels"
                        return resp
                register_tray_labels(locale, labels)
            set_tray_locale(locale)
            # Force a tray menu rebuild so the new labels show immediately.
            with contextlib.suppress(Exception):
                self.app.tray.invalidate_menu_cache()
            resp["type"] = "ack"
            resp["data"] = {"locale": get_tray_locale()}
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "set_tray_locale")
        return resp

    def _handle_set_esc_cancel_paused(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``set_esc_cancel_paused`` IPC command.

        this is now a thin wrapper around the
                ``KeyboardOwnership`` singleton. The ``paused`` flag sets the
                keyboard owner to ``"hotkey_capture"`` (paused=True) or
                ``"normal"`` (paused=False).

                The previous implementation mutated ``app._esc_cancel_paused``
                directly. That attribute is kept as a backward-compat alias
                for existing tests, but the canonical state lives in
                ``KeyboardOwnership``. The hotkey backends consult
                ``KeyboardOwnership.is_hotkey_capture_active()`` on every poll,
                so the moment this IPC lands, the next poll cycle honors it.

                The ``data`` dict should contain ``{"paused": true}`` or
                ``{"paused": false}``.
        """
        try:
            if data is None:
                data = {}
            # validate ``paused`` is a bool via the shared
            # ``_validate_dict_payload`` helper. ``required: False,
            # default: False`` preserves the existing
            # ``test_missing_paused_defaults_to_false`` contract
            # ({} → paused defaults to False → resume normal). The
            # strict ``bool`` type check rejects truthy non-bools
            # (e.g. ``{"paused": "true"}`` → ``invalid_field`` error)
            # that the previous ``bool((data or {}).get("paused",
            # False))`` coercion would have silently accepted as True.
            validated, error = _validate_dict_payload(
                data,
                {
                    "paused": {
                        "type": bool,
                        "required": False,
                        "default": False,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            paused = validated["paused"]
            # update the canonical ownership state.
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            keyboard_ownership().set_owner(
                "hotkey_capture" if paused else "normal",
                reason="frontend hotkey capture" if paused else "capture ended",
            )
            # Backward-compat alias for tests that read app._esc_cancel_paused.
            self.app._esc_cancel_paused = paused
            log.info(
                "[IPC] ESC cancel %s (via frontend hotkey capture mode)",
                "PAUSED" if paused else "RESUMED",
            )
            resp["type"] = "ack"
            resp["data"] = {"paused": paused}
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "set_esc_cancel_paused")
        return resp

    def _handle_show_electron_notification(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``show_electron_notification`` IPC command.

        Push a notification to the Electron UI for
                persistent/critical messages that need longer display
                than the OS-default ~5s tray notification. The Electron
                Notification API supports a ``duration`` parameter (via
                setTimeout auto-close) and can show a toast/banner that
                stays until dismissed.

                SEC-VALIDATE-001 (this review): explicit per-field input
                validation.  Previously the handler coerced every field with
                ``str()`` / ``int()`` / ``bool()`` and relied on the
                surrounding try/except to convert ``ValueError`` (from
                ``int("not-a-number")``) into an "error" response.  That
                produced two failure modes:

                  1. The error message echoed the raw Python exception text
                     back to the client (``"invalid literal for int() with
                     base 10: 'abc'"``) — a minor information leak and a
                     poor UX.
                  2. ``bool("false")`` returned ``True`` because any
                     non-empty string is truthy — a JSON ``"critical":
                     "false"`` from a misbehaving caller would silently
                     escalate the notification to critical.

                We now validate each field explicitly and return a structured
                ``code: "invalid_field"`` error before pushing the event, so
                the client gets a stable contract and no Python internals
                leak.  ``duration_ms`` is also clamped to ``[0, 24h]`` so a
                caller can't schedule a ``setTimeout`` that effectively never
                fires (which would leave a "persistent" notification that the
                user can't dismiss via the auto-close path).

        (IMPROVE-mode run, 2026-07-19): the per-field type
                checks + the ``duration_ms`` clamp now route through
                ``_validate_dict_payload``. The helper's ``clamp_range`` rule
                replaces the inline ``max(0, min(int(duration_ms), 24*60*60*1000))``
                coercion. Two Python-specific gotchas stay inline:

                  * The ``bool`` subclass exclusion for ``duration_ms`` (a
                    bool is an int in Python; without this guard, a caller
                    who swaps the ``critical`` and ``duration_ms`` fields
                    would have ``True`` silently coerced to ``duration_ms: 1``).
                    The helper's ``isinstance(value, (int, float))`` check
                    alone would NOT exclude bool — keeping the pre-check
                    inline is clearer than burying the gotcha in a schema
                    rule.
                  * The ``None`` → default coercion for ``title``/``message``
                    /``duration_ms``/``critical``. The helper's ``default``
                    only fires when the field is ABSENT — a present ``None``
                    fails the type check. Pre-coercing ``None`` to the
                    default keeps the existing "missing or null → default"
                    contract without adding a ``none_to_default`` rule.

                Added ``max_value_len`` rules on
                ``title`` (256 chars) and ``message`` (4096 chars) so a
                misbehaving caller (or a renderer bug) can't push a 1 MB
                notification body that the OS notification API would
                either truncate silently or refuse to display. The caps
                match the OS notification API's practical limits on both
                macOS (``UNNotificationContent.title`` / ``body``) and
                Windows (``ToastNotification`` XML payload).
        """
        try:
            # pre-check the bool subclass exclusion for
            # ``duration_ms`` BEFORE invoking the helper. ``bool`` is
            # a subclass of ``int`` in Python — without this guard,
            # ``duration_ms: True`` would pass the helper's
            # ``isinstance(value, (int, float))`` check and then be
            # coerced to ``duration_ms: 1`` by ``clamp_range``, silently
            # accepting a misbehaving caller who swapped the
            # ``critical`` and ``duration_ms`` fields.
            if isinstance(data, dict) and isinstance(data.get("duration_ms"), bool):
                # Route the envelope through the shared
                # ``_error_response`` helper for shape consistency
                # with the rest of the handler layer; ``field`` is
                # stamped afterward because the helper does not yet
                # accept a ``field`` kwarg.
                _error_response(
                    resp,
                    "'duration_ms' must be a number (milliseconds)",
                    code=ErrorCodes.INVALID_FIELD,
                )
                resp["data"]["field"] = "duration_ms"
                return resp

            # pre-coerce ``None`` values to their defaults so the
            # helper's ``default`` rule (which only fires for ABSENT
            # fields) doesn't reject ``{"title": null}`` as a type
            # error. The previous inline impl had the same coercion.
            if isinstance(data, dict):
                if data.get("title") is None:
                    data = {**data, "title": APP_NAME}
                if data.get("message") is None:
                    data = {**data, "message": ""}
                if data.get("duration_ms") is None:
                    data = {**data, "duration_ms": 0}
                if data.get("critical") is None:
                    data = {**data, "critical": False}

            # route the dict-type + per-field type checks +
            # ``duration_ms`` clamp through ``_validate_dict_payload``.
            # The helper's ``clamp_range`` rule replaces the inline
            # ``max(0, min(int(duration_ms), 24*60*60*1000))`` coercion.
            #
            # Added ``max_value_len`` rules on
            # ``title`` (256 chars) and ``message`` (4096 chars) so a
            # misbehaving caller (or a renderer bug) can't push a 1 MB
            # notification body that the OS notification API would
            # either truncate silently or refuse to display. The caps
            # match the OS notification API's practical limits on both
            # macOS (``UNNotificationContent.title`` / ``body``) and
            # Windows (``ToastNotification`` XML payload).
            validated, error = _validate_dict_payload(
                data,
                {
                    "title": {
                        "type": str,
                        "required": False,
                        "default": APP_NAME,
                        "max_value_len": 256,
                    },
                    "message": {
                        "type": str,
                        "required": False,
                        "default": "",
                        "max_value_len": 4096,
                    },
                    "duration_ms": {
                        "type": (int, float),
                        "required": False,
                        "default": 0,
                        "clamp_range": (0, 24 * 60 * 60 * 1000),
                    },
                    "critical": {"type": bool, "required": False, "default": False},
                },
            )
            if error:
                # The helper's non-dict path returns ``code:
                # "invalid_payload"`` with the ``"data must be an
                # object"`` message — different from the pre-
                # handler-specific ``"show_electron_notification
                # requires data: object"``. The test was updated to
                # assert on ``code`` instead of the message text.
                resp["type"] = "error"
                resp["data"] = error["data"]
                return resp
            assert validated is not None  # narrowed by the error guard above
            title = validated["title"]
            message = validated["message"]
            duration_ms = int(validated["duration_ms"])
            critical = validated["critical"]

            # (session-DE): reject Unicode Cc/Cf control chars in
            # ``title`` / ``message``. The OS notification APIs render
            # ANSI escapes (``\x1b[31m``), terminal bell (``\x07``),
            # newline/CR, RTL overrides (``\u202e``), zero-width marks
            # (``\u200d``), and BOM (``\ufeff``) inconsistently — a
            # misbehaving caller could spoof a critical notification
            # via RTL override, or inject terminal escape sequences
            # into a terminal-based notification viewer. ``\t`` (tab)
            # is explicitly allowed (tabular layout in the message
            # body is common and harmless).
            for fname in ("title", "message"):
                if _has_control_chars(validated.get(fname, "")):
                    # Route the envelope through the shared
                    # ``_error_response`` helper for shape
                    # consistency with the rest of the handler layer;
                    # ``field`` is stamped afterward because the
                    # helper does not yet accept a ``field`` kwarg.
                    _error_response(
                        resp,
                        f"'{fname}' contains a control character",
                        code=ErrorCodes.INVALID_FIELD,
                    )
                    resp["data"]["field"] = fname
                    return resp

            event_bus.publish(
                {
                    # renamed from "electron_notification" to the
                    # platform-agnostic "notification" — the Tauri Rust
                    # host no longer renames the event (it passes through
                    # unchanged), and a Rust-side backward-compat alias
                    # (see src-tauri/src/main.rs) handles old Python
                    # sidecars that still emit the legacy name during
                    # rolling upgrades. The renderer subscribes to
                    # "notification" via the generic `python-event`
                    # envelope, not by name.
                    "type": "notification",
                    "data": {
                        "title": title,
                        "message": message,
                        "duration_ms": duration_ms,
                        "critical": critical,
                    },
                }
            )
            resp["type"] = "ack"
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "show_electron_notification")
        return resp
