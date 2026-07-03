"""Test fixtures package.

Contains both binary fixture files (WAV audio samples — see README.md)
and Python fixture modules used by tests:

- :mod:`tests.fixtures.ipc_test_helpers` — DI-mode fakes for
  :class:`voice_typer.server.ipc_server.IPCServer` (ARCH-REFAC-004).
- :mod:`tests.fixtures.generate_fixture` — script to regenerate the
  WAV fixtures deterministically.

This ``__init__.py`` makes ``tests.fixtures`` a proper package so that
imports like ``from tests.fixtures.ipc_test_helpers import ...`` work
reliably across Python versions and test runners.
"""
