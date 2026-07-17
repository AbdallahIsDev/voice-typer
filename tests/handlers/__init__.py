"""CR-12: dedicated unit tests for the 14 IPC handler mixins.

Each ``test_<name>_handlers.py`` file in this package targets one
handler mixin module from ``voice_typer/server/handlers/``.  The
tests use the canonical DI helpers in
``tests/fixtures/ipc_test_helpers.py`` (``make_fake_app`` /
``make_fake_service``) to inject a mock ``service`` and assert on
the handler's response shape for each validation path — without
coupling to the real ``VoiceTyperApp`` / ``VoiceTyperService``
internals.

The handler mixins are otherwise only exercised indirectly via
integration tests (``tests/test_server.py``,
``tests/test_ipc_dispatch_errors.py``, etc.), which makes regression
hunting on a single handler's response shape slow.  These unit tests
fill that gap with focused happy-path + validation-error coverage.
"""
